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

from . import services
from .models import Spot, Visitor


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

    def test_activity_returns_only_confirmed(self):
        Spot.objects.create(
            brand_name='C1', size='medium', status='confirmed', price_paid=1200,
            width_cm=6.0, height_cm=5.0,
        )
        Spot.objects.create(
            brand_name='P', size='small', status='pending', price_paid=500,
            width_cm=4.5, height_cm=4.5,
        )
        resp = self.client.get(reverse('spots-activity'))
        self.assertEqual(resp.json()['count'], 1)
        self.assertEqual(resp.json()['results'][0]['brand_name'], 'C1')


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
