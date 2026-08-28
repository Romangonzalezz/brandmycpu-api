import base64
import hashlib
import hmac
import json
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

    def test_create_spot_passes_reference_to_dodo(self):
        with mock.patch.object(
            services, 'create_checkout',
            return_value={'checkout_url': 'https://x', 'session_id': 's'},
        ) as mocked:
            self.client.post(
                reverse('spots-list'),
                data=json.dumps({'brand_name': 'B', 'size': 'small', 'offered_price': 5.0}),
                content_type='application/json',
            )
        kwargs = mocked.call_args.kwargs
        spot = Spot.objects.get()
        self.assertEqual(kwargs['reference'], str(spot.id))

    def test_price_below_minimum_rejected(self):
        resp = self.client.post(
            reverse('spots-list'),
            data=json.dumps({'brand_name': 'X', 'size': 'large', 'offered_price': 3.0}),
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