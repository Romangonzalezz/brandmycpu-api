import base64
import hashlib
import hmac
import json
import tempfile
import time
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services, views
from .models import Click, Giveaway, Spot, Visitor


def _webhook_headers(body: bytes, secret: str, msg_id='msg_test'):
    """Construye headers Standard Webhooks válidos para `body`."""
    ts = str(int(time.time()))
    key = base64.b64decode(secret.removeprefix('whsec_'))
    to_sign = msg_id.encode() + b'.' + ts.encode() + b'.' + body
    sig = base64.b64encode(
        hmac.new(key, to_sign, hashlib.sha256).digest()
    ).decode()
    return {
        'msg_id': msg_id,
        'ts': ts,
        'signature': f'v1,{sig}',
    }


def _post_webhook(client, body: bytes, secret: str):
    h = _webhook_headers(body, secret)
    return client.post(
        reverse('spots-webhook'),
        data=body,
        content_type='application/json',
        HTTP_WEBHOOK_ID=h['msg_id'],
        HTTP_WEBHOOK_TIMESTAMP=h['ts'],
        HTTP_WEBHOOK_SIGNATURE=h['signature'],
    )


class VisitorEndpointsTests(TestCase):
    def test_heartbeat_creates_visitor(self):
        resp = self.client.post(
            reverse('visitor-heartbeat'),
            data=json.dumps({'session_id': 'abc-123'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['ok'], True)
        self.assertEqual(Visitor.objects.count(), 1)

    def test_heartbeat_requires_session_id(self):
        resp = self.client.post(
            reverse('visitor-heartbeat'), data={}, content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_count_live_and_total(self):
        now = timezone.now()
        Visitor.objects.create(session_id='live-1')
        Visitor.objects.create(session_id='live-2')
        old = Visitor.objects.create(session_id='old-1')
        # auto_now pisa la fecha al crear; la fijamos vía update()
        Visitor.objects.filter(pk=old.pk).update(last_seen=now - timedelta(minutes=45))
        resp = self.client.get(reverse('visitor-count'))
        self.assertEqual(resp.json()['live'], 2)
        self.assertEqual(resp.json()['total'], 3)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SpotEndpointTests(TestCase):
    def test_create_spot_returns_payment_url(self):
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://test.dodopayments.com/checkout/x', 'session_id': 'sess_1'},
        ):
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'TestBrand',
                    'size': 'small',
                    'position_x': 0.4,
                    'position_y': 0.6,
                    'offered_price': 5.0,
                    'website': 'https://testbrand.dev',
                    'x_handle': 'testbrand',
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn('id', body)
        self.assertEqual(body['payment_url'], 'https://test.dodopayments.com/checkout/x')
        spot = Spot.objects.get(pk=body['id'])
        self.assertEqual(spot.price_paid, 500)  # $5 en centavos
        self.assertEqual(spot.width_cm, 4.5)
        self.assertEqual(spot.website, 'https://testbrand.dev')
        self.assertEqual(spot.x_handle, 'testbrand')

    def test_create_spot_passes_reference_to_dodo(self):
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://x', 'session_id': 's'},
        ) as mocked:
            self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'B', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://b.dev',
                }),
                content_type='application/json',
            )
        kwargs = mocked.call_args.kwargs
        spot = Spot.objects.get()
        self.assertEqual(kwargs['reference'], str(spot.id))

    @override_settings(FAKE_PAYMENTS=True)
    def test_fake_payments_confirms_without_calling_dodo(self):
        """Modo demo: mismo recorrido, sin proveedor de pago."""
        with mock.patch.object(services, 'create_checkout') as mocked:
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Demo', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://demo.dev',
                    'position_x': 0.2, 'position_y': 0.2,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 201)
        mocked.assert_not_called()
        spot = Spot.objects.get(pk=resp.json()['id'])
        self.assertEqual(spot.status, 'confirmed')
        self.assertIn(f'?paid={spot.id}', resp.json()['payment_url'])

    def test_size_decides_the_dimensions_not_the_buyer(self):
        """Un 'small' de 40x40 cm por $5 sería medio vidrio al precio del
        hueco más barato. Las medidas las pone el servidor."""
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://x', 'session_id': 's'},
        ):
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Vivo', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://vivo.dev',
                    'width_cm': 40, 'height_cm': 40,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 201)
        spot = Spot.objects.get(pk=resp.json()['id'])
        self.assertEqual((spot.width_cm, spot.height_cm), (4.5, 4.5))

    def test_the_whole_sticker_has_to_fit_on_the_glass(self):
        """El centro dentro de 0..1 no alcanza: arrastrando libre se llega al
        filo y medio sticker queda colgando fuera del vidrio."""
        for x, y in ((0.99, 0.5), (0.01, 0.5), (0.5, 0.99), (0.5, 0.01)):
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Borde', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://borde.dev',
                    'position_x': x, 'position_y': y,
                }),
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 400, (x, y))
        self.assertFalse(Spot.objects.exists())

    def test_a_free_position_off_the_grid_is_allowed(self):
        """La grilla es del frontend. El backend sólo pide que entre y que no
        pise a nadie, asi que arrastrar a cualquier punto libre vale."""
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://x', 'session_id': 's'},
        ):
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Libre', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://libre.dev',
                    'position_x': 0.4137, 'position_y': 0.6289,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 201)

    def test_position_has_to_be_on_the_glass(self):
        for x, y in [(5.0, 0.5), (-1.0, 0.5), (0.5, 9.0)]:
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Fuera', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://fuera.dev',
                    'position_x': x, 'position_y': y,
                }),
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 400, f'{x},{y} entró')
        self.assertFalse(Spot.objects.exists())

    def test_website_is_required(self):
        """Sin sitio no hay tarjeta de sponsor, así que el spot no se crea."""
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'X', 'size': 'small', 'offered_price': 5.0,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('website', resp.json())
        self.assertFalse(Spot.objects.exists())

    def test_cannot_take_a_sold_spot_without_beating_its_price(self):
        """Un small encima de un large vendido a $20 no entra por $5.

        El frontend esconde el hueco, pero eso corre en el navegador del
        comprador: acá se decide. Empatar tampoco alcanza, hay que superar.
        """
        Spot.objects.create(
            brand_name='Grande', size='large', width_cm=9.5, height_cm=4.0,
            position_x=0.5, position_y=0.5, price_paid=2000, status='confirmed',
        )
        for offer in (5.0, 20.0):  # por debajo, y exactamente igual
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Chico', 'size': 'small', 'offered_price': offer,
                    'website': 'https://chico.dev',
                    'position_x': 0.5, 'position_y': 0.5,
                }),
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 400, offer)
            self.assertIn('offered_price', resp.json())
        self.assertEqual(Spot.objects.count(), 1)

    def test_outbid_has_to_beat_the_sum_of_everything_it_covers(self):
        """Un large tapa tres smalls: hay que superar el total, no cada uno.

        Superando de a uno, $21 se llevaría $60 de vidrio.
        """
        for x in (0.30, 0.4326, 0.5651):
            Spot.objects.create(
                brand_name=f'S{x}', size='small', width_cm=4.5, height_cm=4.5,
                position_x=x, position_y=0.5, price_paid=2000, status='confirmed',
            )
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'Grande', 'size': 'large', 'offered_price': 21.0,
                'website': 'https://grande.dev',
                'position_x': 0.4326, 'position_y': 0.5,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('offered_price', resp.json())

    def test_a_live_checkout_cannot_be_outbid_at_any_price(self):
        """Un pending no es una oferta: la plata no entró y el lugar está
        reservado. Sin esto, el segundo comprador desplaza a alguien que ni
        llegó a estar en el vidrio."""
        Spot.objects.create(
            brand_name='Comprando', size='small', width_cm=4.5, height_cm=4.5,
            position_x=0.5, position_y=0.5, price_paid=500, status='pending',
        )
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'Rico', 'size': 'small', 'offered_price': 500.0,
                'website': 'https://rico.dev',
                'position_x': 0.5, 'position_y': 0.5,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('position_x', resp.json())

    @override_settings(FAKE_PAYMENTS=True)
    def test_settling_an_outbid_displaces_the_previous_sponsor(self):
        """El desplazado sale del vidrio, no se borra, y no se le devuelve."""
        loser = Spot.objects.create(
            brand_name='Antes', size='small', width_cm=4.5, height_cm=4.5,
            position_x=0.5, position_y=0.5, price_paid=500, status='confirmed',
        )
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'Despues', 'size': 'small', 'offered_price': 6.0,
                'website': 'https://despues.dev',
                'position_x': 0.5, 'position_y': 0.5,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)

        loser.refresh_from_db()
        winner = Spot.objects.get(pk=resp.json()['id'])
        self.assertEqual(winner.status, 'confirmed')
        self.assertEqual(loser.status, 'outbid')
        # La fila sigue ahí y con su monto: no hubo reembolso.
        self.assertEqual(loser.price_paid, 500)

        # Y su plata sigue contando: la barra no puede bajar por dinero que
        # entró y nunca se devolvió.
        with override_settings(SPOT_GOAL=10000):
            self.assertEqual(self.client.get(reverse('goal')).json()['raised'], 1100)

    @override_settings(FAKE_PAYMENTS=True)
    def test_a_displaced_spot_frees_its_place_for_the_next_buyer(self):
        """Una vez desplazado deja de ocupar: si no, el hueco queda trabado por
        alguien que ya no está en el vidrio."""
        Spot.objects.create(
            brand_name='Antes', size='small', width_cm=4.5, height_cm=4.5,
            position_x=0.5, position_y=0.5, price_paid=500, status='outbid',
        )
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'Nuevo', 'size': 'small', 'offered_price': 5.0,
                'website': 'https://nuevo.dev',
                'position_x': 0.5, 'position_y': 0.5,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)

    def test_a_free_corner_next_to_it_still_works(self):
        Spot.objects.create(
            brand_name='Grande', size='large', width_cm=9.5, height_cm=4.0,
            position_x=0.5, position_y=0.5, price_paid=2000, status='confirmed',
        )
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://x', 'session_id': 's'},
        ):
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Chico', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://chico.dev',
                    'position_x': 0.1, 'position_y': 0.1,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 201)

    def test_abandoned_checkout_frees_the_spot_again(self):
        """Un pending viejo no puede bloquear un hueco para siempre."""
        old = Spot.objects.create(
            brand_name='Fantasma', size='small', width_cm=4.5, height_cm=4.5,
            position_x=0.5, position_y=0.5, price_paid=500, status='pending',
        )
        Spot.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://x', 'session_id': 's'},
        ):
            resp = self.client.post(
                reverse('spots-list'),
                data=json.dumps({
                    'brand_name': 'Real', 'size': 'small', 'offered_price': 5.0,
                    'website': 'https://real.dev',
                    'position_x': 0.5, 'position_y': 0.5,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 201)

    def test_a_checkout_in_progress_holds_the_spot(self):
        Spot.objects.create(
            brand_name='Comprando', size='small', width_cm=4.5, height_cm=4.5,
            position_x=0.5, position_y=0.5, price_paid=500, status='pending',
        )
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'Otro', 'size': 'small', 'offered_price': 5.0,
                'website': 'https://otro.dev',
                'position_x': 0.5, 'position_y': 0.5,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_logo_url_is_absolute(self):
        """Relativa se resolvería contra el dominio del frontend, no la API."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        Spot.objects.create(
            brand_name='ConLogo', size='small', width_cm=4.5, height_cm=4.5,
            price_paid=500, status='placed',
            logo=SimpleUploadedFile('l.png', b'x', content_type='image/png'),
        )
        url = self.client.get(reverse('spots-list')).json()[0]['logo_url']
        self.assertTrue(url.startswith('http://testserver/media/logos/'), url)

    def test_price_below_minimum_rejected(self):
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({
                'brand_name': 'X', 'size': 'large', 'offered_price': 3.0,
                'website': 'https://x.dev',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_list_filter_by_status(self):
        Spot.objects.create(
            brand_name='A', size='small', status='placed',
            width_cm=4.5, height_cm=4.5, price_paid=500,
        )
        Spot.objects.create(
            brand_name='B', size='small', status='pending',
            width_cm=4.5, height_cm=4.5, price_paid=500,
        )
        resp = self.client.get(reverse('spots-list'), {'status': 'placed'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['brand_name'], 'A')

    def test_activity_shows_what_was_bought_and_nothing_else(self):
        """`placed` cuenta. Filtrar sólo `confirmed` sacaba del feed a un
        sticker justo cuando se lo pegaba de verdad, así que la lista se
        vaciaba a medida que se cumplían los pedidos."""
        for name, status in (
            ('C1', 'confirmed'),
            ('Pegado', 'placed'),
            ('P', 'pending'),
            ('Perdido', 'outbid'),
        ):
            Spot.objects.create(
                brand_name=name, size='small', status=status, price_paid=500,
                width_cm=4.5, height_cm=4.5,
            )
        body = self.client.get(reverse('spots-activity')).json()
        self.assertEqual(body['count'], 2)
        self.assertEqual(
            {r['brand_name'] for r in body['results']}, {'C1', 'Pegado'}
        )

    def test_the_proof_photo_reaches_the_page(self):
        """Sin esto `status='placed'` es sólo una afirmación mía."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        png = base64.b64decode(
            b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ'
            b'DwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
        )
        s = Spot.objects.create(
            brand_name='ConFoto', size='small', status='placed', price_paid=500,
            width_cm=4.5, height_cm=4.5,
        )
        s.placed_photo = SimpleUploadedFile('glass.png', png, content_type='image/png')
        s.save()

        row = next(
            r for r in self.client.get(reverse('spots-list')).json()
            if r['brand_name'] == 'ConFoto'
        )
        self.assertTrue(row['placed_photo_url'].startswith('http'))
        self.assertIn('placed/', row['placed_photo_url'])


class WebhookTests(TestCase):
    def setUp(self):
        self.secret = 'whsec_' + base64.b64encode(b'k' * 32).decode()

    def test_webhook_requires_valid_signature(self):
        body = json.dumps({'type': 'payment.succeeded', 'data': {}}).encode()
        with override_settings(DODO_WEBHOOK_SECRET=self.secret):
            resp = self.client.post(
                reverse('spots-webhook'),
                data=body,
                content_type='application/json',
                HTTP_WEBHOOK_ID='msg_test',
                HTTP_WEBHOOK_TIMESTAMP=str(int(time.time())),
                HTTP_WEBHOOK_SIGNATURE='v1,AAAA',
            )
            self.assertEqual(resp.status_code, 400)

    def test_webhook_rejects_replay(self):
        body = json.dumps({'type': 'payment.succeeded', 'data': {}}).encode()
        old_ts = str(int(time.time()) - 600)  # 10 min atrás
        key = base64.b64decode(self.secret.removeprefix('whsec_'))
        to_sign = b'msg' + b'.' + old_ts.encode() + b'.' + body
        sig = base64.b64encode(hmac.new(key, to_sign, hashlib.sha256).digest()).decode()
        with override_settings(DODO_WEBHOOK_SECRET=self.secret):
            resp = self.client.post(
                reverse('spots-webhook'), data=body, content_type='application/json',
                HTTP_WEBHOOK_ID='msg', HTTP_WEBHOOK_TIMESTAMP=old_ts,
                HTTP_WEBHOOK_SIGNATURE=f'v1,{sig}',
            )
            self.assertEqual(resp.status_code, 400)

    def test_webhook_confirms_spot(self):
        spot = Spot.objects.create(
            brand_name='Pay', size='small', status='pending',
            width_cm=4.5, height_cm=4.5, price_paid=500,
            payment_id='pay_123',
        )
        body = json.dumps({
            'type': 'payment.succeeded',
            'data': {
                'payment_id': 'pay_123',
                'metadata': {'reference': str(spot.id)},
                'settlement_amount': 500,
            },
        }).encode()
        with override_settings(DODO_WEBHOOK_SECRET=self.secret):
            resp = _post_webhook(self.client, body, self.secret)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['updated'], True)
        spot.refresh_from_db()
        self.assertEqual(spot.status, 'confirmed')

    def test_webhook_retry_is_noop(self):
        spot = Spot.objects.create(
            brand_name='Pay', size='small', status='confirmed',
            width_cm=4.5, height_cm=4.5, price_paid=500, payment_id='pay_2',
        )
        body = json.dumps({
            'type': 'payment.succeeded',
            'data': {'payment_id': 'pay_2', 'metadata': {'reference': str(spot.id)}},
        }).encode()
        with override_settings(DODO_WEBHOOK_SECRET=self.secret):
            resp = _post_webhook(self.client, body, self.secret)
        self.assertEqual(resp.json()['updated'], False)  # ya estaba confirmado


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ClickTests(TestCase):
    def setUp(self):
        self.spot = Spot.objects.create(
            brand_name='Sponsor', website='https://sponsor.dev', size='small',
            width_cm=4.5, height_cm=4.5, price_paid=500, status='confirmed',
        )

    def _click(self, ip='9.9.9.9'):
        return self.client.post(
            reverse('spots-click', args=[self.spot.id]), REMOTE_ADDR=ip
        )

    def test_one_click_per_ip_forever(self):
        """El número por el que un sponsor juzga tiene que ser real."""
        self.assertTrue(self._click().json()['counted'])
        for _ in range(5):
            self.assertFalse(self._click().json()['counted'])
        self.spot.refresh_from_db()
        self.assertEqual(self.spot.clicks_count, 1)
        self.assertEqual(Click.objects.count(), 1)

    def test_a_different_address_is_a_different_click(self):
        self._click('1.1.1.1')
        self._click('2.2.2.2')
        self.spot.refresh_from_db()
        self.assertEqual(self.spot.clicks_count, 2)

    def test_the_address_is_never_stored(self):
        """Sólo el hash con sal. Guardar la IP haría de esto otra cosa."""
        self._click('8.8.8.8')
        click = Click.objects.get()
        self.assertNotIn('8.8.8.8', click.ip_hash)
        self.assertEqual(len(click.ip_hash), 64)

    def test_clicks_on_a_spot_that_is_not_on_the_glass_do_not_count(self):
        pend = Spot.objects.create(
            brand_name='P', size='small', width_cm=4.5, height_cm=4.5,
            price_paid=500, status='pending', position_x=0.2, position_y=0.2,
        )
        resp = self.client.post(reverse('spots-click', args=[pend.id]))
        self.assertFalse(resp.json()['counted'])
        self.assertEqual(Click.objects.count(), 0)

    def test_the_count_reaches_the_page(self):
        self._click('4.4.4.4')
        row = self.client.get(reverse('spots-list')).json()[0]
        self.assertEqual(row['clicks_count'], 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), GIVEAWAY_SEATS=7)
class GiveawayTests(TestCase):
    PNG = base64.b64decode(
        b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ'
        b'DwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    )

    def _payload(self, **over):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {
            'brand_name': 'Regalado', 'website': 'https://regalado.dev',
            'size': 'small', 'position_x': '0.5', 'position_y': '0.5',
            'tweet_url': 'https://x.com/someone/status/1234567890',
            'email': 'a@b.com',
            'logo': SimpleUploadedFile('l.png', self.PNG, content_type='image/png'),
        }
        data.update(over)
        return data

    def _verified(self, handle='someone'):
        from .tweets import VerifiedTweet
        return mock.patch.object(
            views, 'verify_tweet',
            return_value=VerifiedTweet(tweet_id='123', author_handle=handle, text='ok'),
        )

    def test_seats_left_is_public(self):
        body = self.client.get(reverse('giveaway')).json()
        self.assertEqual(body['seatsLeft'], 7)
        # Lo unico que el post tiene que nombrar. El asiento ya no va en el
        # texto: lo ata el constraint sobre el id del post.
        self.assertEqual(body['phrase'], 'BrandMyCPU')

    def test_a_verified_post_takes_a_seat(self):
        with self._verified():
            resp = self.client.post(reverse('giveaway'), self._payload())
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['seat'], 1)
        self.assertEqual(resp.json()['seatsLeft'], 6)

        spot = Spot.objects.get(brand_name='Regalado')
        self.assertEqual(spot.status, 'confirmed')
        # Cero y a propósito: nadie pagó, así que esto no puede mover el goal.
        self.assertEqual(spot.price_paid, 0)
        self.assertTrue(spot.logo)

    def test_a_free_spot_never_moves_the_goal(self):
        """Si un regalo sumara al total recaudado, el total sería mentira."""
        with self._verified():
            self.client.post(reverse('giveaway'), self._payload())
        self.assertEqual(self.client.get(reverse('goal')).json()['raised'], 0)

    def test_without_the_post_there_is_no_seat(self):
        from .tweets import TweetError
        with mock.patch.object(
            views, 'verify_tweet', side_effect=TweetError('That post does not mention it.')
        ):
            resp = self.client.post(reverse('giveaway'), self._payload())
        self.assertEqual(resp.status_code, 400)
        # Ni asiento gastado ni Spot huérfano en el vidrio.
        self.assertEqual(Giveaway.objects.count(), 0)
        self.assertEqual(Spot.objects.count(), 0)

    def test_a_free_spot_cannot_take_a_paid_one(self):
        Spot.objects.create(
            brand_name='Pagó', size='small', width_cm=4.5, height_cm=4.5,
            position_x=0.5, position_y=0.5, price_paid=2000, status='placed',
        )
        with self._verified():
            resp = self.client.post(reverse('giveaway'), self._payload())
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Giveaway.objects.count(), 0)

    def test_the_whole_sticker_still_has_to_fit(self):
        """Misma regla que la compra: el lugar gratis no afloja la geometría."""
        with self._verified():
            resp = self.client.post(
                reverse('giveaway'), self._payload(position_x='0.99')
            )
        self.assertEqual(resp.status_code, 400)

    @override_settings(GIVEAWAY_SEATS=1)
    def test_the_last_seat_can_only_be_taken_once(self):
        with self._verified():
            self.assertEqual(
                self.client.post(reverse('giveaway'), self._payload()).status_code, 201
            )
            resp = self.client.post(
                reverse('giveaway'),
                self._payload(brand_name='Segundo', position_x='0.2', position_y='0.2'),
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(Giveaway.objects.count(), 1)

    def test_one_post_pays_for_one_seat(self):
        """Sin esto, copiar el post de otro se presenta siete veces y se lleva
        los siete lugares. Es lo que permite que el texto no tenga que llevar
        el número del asiento."""
        from .tweets import VerifiedTweet
        same = mock.patch.object(
            views, 'verify_tweet',
            return_value=VerifiedTweet(tweet_id='999', author_handle='x', text=''),
        )
        with same:
            first = self.client.post(reverse('giveaway'), self._payload())
            second = self.client.post(
                reverse('giveaway'),
                self._payload(brand_name='Copión', position_x='0.2', position_y='0.2'),
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(Giveaway.objects.count(), 1)
        # Y no quedó un sticker suelto en el vidrio del reclamo que no entró.
        self.assertEqual(Spot.objects.count(), 1)

    @override_settings(GIVEAWAY_SEATS=0)
    def test_zero_seats_is_the_campaign_being_off(self):
        with self._verified():
            resp = self.client.post(reverse('giveaway'), self._payload())
        self.assertEqual(resp.status_code, 409)


class TweetProofTests(TestCase):
    """Lo único que separa siete lugares gratis de siete bots."""

    URL = 'https://x.com/someone/status/1234567890'

    def _oembed(self, text):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {
            'html': f'<blockquote><p>{text}</p></blockquote>',
            'author_url': 'https://twitter.com/someone',
        }
        return mock.patch('spots.tweets.requests.get', return_value=resp)

    def test_naming_the_site_proves_the_claim(self):
        from .tweets import verify_tweet
        with self._oembed('I just claimed my spot on BrandMyCPU'):
            tweet = verify_tweet(tweet_url=self.URL, seat=3)
        self.assertEqual(tweet.tweet_id, '1234567890')
        self.assertEqual(tweet.author_handle, 'someone')

    def test_spelling_spacing_and_case_do_not_cost_a_spot(self):
        """Nadie debería perder un lugar por un guion."""
        from .tweets import verify_tweet
        for text in (
            'MY SPOT ON BRAND-MY-CPU',
            'got on brand my cpu today',
        ):
            with self._oembed(text):
                self.assertTrue(verify_tweet(tweet_url=self.URL, seat=3).tweet_id)

    def test_naming_nothing_proves_nothing(self):
        from .tweets import TweetError, verify_tweet
        with self._oembed('what a nice PC, I want one'):
            with self.assertRaises(TweetError):
                verify_tweet(tweet_url=self.URL, seat=3)

    def test_a_link_that_is_not_a_post_is_refused_before_any_request(self):
        from .tweets import TweetError, verify_tweet
        for bad in ('https://x.com/romg_dev', 'brandmycpu.lol', ''):
            with self.assertRaises(TweetError):
                verify_tweet(tweet_url=bad, seat=1)

    def test_our_timeout_is_never_told_as_their_post_missing(self):
        """Nunca decirle a alguien que su post real no existe porque a nosotros
        se nos venció una request."""
        from .tweets import TweetError, verify_tweet
        import requests as rq
        with mock.patch('spots.tweets.requests.get', side_effect=rq.Timeout()):
            with self.assertRaises(TweetError) as ctx:
                verify_tweet(tweet_url=self.URL, seat=1)
        self.assertIn('Try again', str(ctx.exception))

    def test_a_private_or_deleted_post_is_not_a_public_statement(self):
        from .tweets import TweetError, verify_tweet
        with mock.patch('spots.tweets.requests.get', return_value=mock.Mock(status_code=404)):
            with self.assertRaises(TweetError):
                verify_tweet(tweet_url=self.URL, seat=1)


class GoalTests(TestCase):
    def test_goal_calculation(self):
        Spot.objects.create(
            brand_name='A', size='small', status='confirmed', price_paid=500,
            width_cm=4.5, height_cm=4.5,
        )
        Spot.objects.create(
            brand_name='B', size='small', status='pending', price_paid=500,
            width_cm=4.5, height_cm=4.5,
        )
        with override_settings(SPOT_GOAL=1000):
            resp = self.client.get(reverse('goal'))
            body = resp.json()
            self.assertEqual(body['raised'], 500)
            self.assertEqual(body['percentage'], 50)

# Forma real de un `payment.succeeded` de Dodo, con los datos reemplazados por
# inventados: lo que hay que fijar acá es la ESTRUCTURA (dónde vive la
# reference, que los montos son centavos, dónde está el payment_id), no los
# ids ni los datos de facturación de nadie.
#
# Los campos que no leemos están igual a propósito: si Dodo agrega o renombra
# algo alrededor, el test sigue pasando y nos dice que nuestro parseo sólo
# depende de lo que realmente usa.
REAL_DODO_PAYLOAD = {
    "data": {
        "tax": 0,
        "status": "succeeded",
        "billing": {"city": "Springfield", "state": "Oregon", "street": "1 Test St",
                    "country": "US", "zipcode": "97477"},
        "refunds": [],
        "brand_id": "bus_testbusiness000000",
        "currency": "USD",
        "customer": {"name": "Test Brand", "email": "buyer@example.com",
                     "metadata": {}, "customer_id": "cus_testcustomer000000",
                     "phone_number": None},
        "disputes": [],
        "metadata": {"host": "example.com",
                     "reference": "62f88772-538a-4e85-9e00-3d0c2969afae"},
        "card_type": None,
        "discounts": None,
        "created_at": "2026-08-26T22:31:21.560290Z",
        "error_code": None,
        "invoice_id": "inv_testinvoice0000000",
        "payment_id": "pay_testpayment0000000",
        "updated_at": None,
        "business_id": "bus_testbusiness000000",
        "discount_id": None,
        "card_network": None,
        "payload_type": "Payment",
        "product_cart": [{"quantity": 1, "product_id": "pdt_testproduct0000000"}],
        "total_amount": 100,
        "error_message": None,
        "refund_status": None,
        "retry_attempt": 0,
        "card_last_four": None,
        "payment_method": "wallet",
        "settlement_tax": 0,
        "subscription_id": None,
        "card_holder_name": None,
        "payment_provider": "dodo",
        "payment_method_id": None,
        "settlement_amount": 100,
        "checkout_session_id": "cks_testcheckout000000",
        "payment_method_type": "apple_pay",
        "settlement_currency": "USD",
        "card_issuing_country": None,
        "custom_field_responses": None,
        "is_update_payment_method": False,
        "digital_products_delivered": False,
    },
    "type": "payment.succeeded",
    "timestamp": "2026-08-26T22:31:49.847323Z",
    "business_id": "bus_testbusiness000000",
}

SECRET = 'whsec_' + base64.b64encode(b'0123456789abcdef').decode()


@override_settings(DODO_WEBHOOK_SECRET=SECRET)
class RealDodoPayloadTests(TestCase):
    """El parseo, contra la forma real del webhook de Dodo."""

    def _post(self, payload):
        body = json.dumps(payload).encode()
        h = _webhook_headers(body, SECRET)
        return self.client.post(
            reverse('spots-webhook'), data=body,
            content_type='application/json',
            HTTP_WEBHOOK_ID=h['msg_id'],
            HTTP_WEBHOOK_TIMESTAMP=h['ts'],
            HTTP_WEBHOOK_SIGNATURE=h['signature'],
        )

    def test_parses_every_field_we_read(self):
        body = json.dumps(REAL_DODO_PAYLOAD).encode()
        h = _webhook_headers(body, SECRET)
        event = services.parse_webhook(body, {
            'webhook-id': h['msg_id'],
            'webhook-timestamp': h['ts'],
            'webhook-signature': h['signature'],
        })
        self.assertEqual(event['event_type'], 'payment.succeeded')
        self.assertTrue(event['is_succeeded'])
        self.assertFalse(event['is_failed'])
        self.assertEqual(event['payment_id'], 'pay_testpayment0000000')
        # La referencia vuelve del metadata que mandamos en el checkout.
        self.assertEqual(event['reference'], '62f88772-538a-4e85-9e00-3d0c2969afae')
        # Dodo manda centavos: $1 = 100.
        self.assertEqual(event['amount_cents'], 100)
        self.assertEqual(event['event_id'], h['msg_id'])

    def test_confirms_the_spot_it_points_at(self):
        """Con nuestra reference (el id del spot) el pago cierra el círculo."""
        spot = Spot.objects.create(
            brand_name='Acme', size='small', width_cm=4.5, height_cm=4.5,
            price_paid=500, status='pending',
        )
        payload = json.loads(json.dumps(REAL_DODO_PAYLOAD))
        payload['data']['metadata']['reference'] = str(spot.id)

        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        spot.refresh_from_db()
        self.assertEqual(spot.status, 'confirmed')
        self.assertEqual(spot.payment_id, 'pay_testpayment0000000')

    def test_unknown_reference_is_accepted_and_ignored(self):
        """Un UUID de otra app no es nuestro id: no explota ni toca nada."""
        resp = self._post(REAL_DODO_PAYLOAD)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['updated'])

    def test_datafast_gets_the_settled_amount_once(self):
        spot = Spot.objects.create(
            brand_name='Acme', size='small', width_cm=4.5, height_cm=4.5,
            price_paid=500, status='pending',
            datafast_visitor_id='visitor-abc',
        )
        payload = json.loads(json.dumps(REAL_DODO_PAYLOAD))
        payload['data']['metadata']['reference'] = str(spot.id)

        with mock.patch.object(services, 'report_payment_to_datafast') as mocked:
            self._post(payload)
            # Dodo reintenta: el segundo intento no debe volver a reportar.
            self._post(payload)

        self.assertEqual(mocked.call_count, 1)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs['amount_cents'], 100)
        self.assertEqual(kwargs['transaction_id'], 'pay_testpayment0000000')
        self.assertEqual(kwargs['visitor_id'], 'visitor-abc')

    def _confirmed_spot(self):
        spot = Spot.objects.create(
            brand_name='Acme', size='small', width_cm=4.5, height_cm=4.5,
            price_paid=500, status='pending',
        )
        payload = json.loads(json.dumps(REAL_DODO_PAYLOAD))
        payload['data']['metadata']['reference'] = str(spot.id)
        self._post(payload)
        spot.refresh_from_db()
        self.assertEqual(spot.status, 'confirmed')
        return spot, payload

    def test_refund_takes_the_money_back_out_of_the_goal(self):
        spot, payload = self._confirmed_spot()
        self.assertEqual(self.client.get(reverse('goal')).json()['raised'], 500)

        payload['data']['metadata']['reference'] = str(spot.id)
        payload['type'] = 'refund.succeeded'
        self._post(payload)

        spot.refresh_from_db()
        self.assertEqual(spot.status, 'pending')
        self.assertEqual(self.client.get(reverse('goal')).json()['raised'], 0)

    def test_dispute_pulls_the_spot_too(self):
        spot, payload = self._confirmed_spot()
        payload['type'] = 'dispute.opened'
        self._post(payload)
        spot.refresh_from_db()
        self.assertEqual(spot.status, 'pending')

    def test_late_failure_cannot_undo_a_confirmed_payment(self):
        """Un `failed` que llega tarde no tumba un cobro que sí prosperó."""
        spot, payload = self._confirmed_spot()
        payload['type'] = 'payment.failed'
        self._post(payload)
        spot.refresh_from_db()
        self.assertEqual(spot.status, 'confirmed')
