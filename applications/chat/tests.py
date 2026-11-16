from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.test import override_settings
from django.urls import reverse

from applications.users.models import User

from .models import Conversation, Message, Product
from .inventory import search_products as search_inventory, search_terms
from .services import answer_sheet_message
from .providers import ProviderError
from types import SimpleNamespace


class ConversationAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('access@test.com')
        cls.other_user = User.objects.create_user('other-access@test.com')
        cls.owned = Conversation.objects.create(user=cls.user)
        cls.foreign = Conversation.objects.create(user=cls.other_user)
        cls.anonymous = Conversation.objects.create()
        cls.unrelated_anonymous = Conversation.objects.create()
        Message.objects.create(conversation=cls.owned, role=Message.USER, content='Mensaje propio')
        Message.objects.create(conversation=cls.foreign, role=Message.USER, content='Mensaje privado ajeno')
        Message.objects.create(conversation=cls.anonymous, role=Message.USER, content='Mensaje anónimo autorizado')

    def authorize_session(self, *ids):
        session = self.client.session
        session['anonymous_chat_conversations'] = [str(value) for value in ids]
        session.save()

    def test_malformed_ids_return_404_for_authenticated_and_anonymous_visitors(self):
        for authenticated in [False, True]:
            if authenticated:
                self.client.force_login(self.user)
            for value in ['hola', '123', '   ', 'not-a-uuid', '0' * 33]:
                with self.subTest(authenticated=authenticated, value=value):
                    response = self.client.get(reverse('chat:index'), {'conversation': value})
                    self.assertEqual(response.status_code, 404)

    def test_authenticated_visitor_cannot_open_foreign_or_anonymous_conversations(self):
        self.client.force_login(self.user)
        self.authorize_session(self.anonymous.pk)
        for value in [self.foreign.pk, self.anonymous.pk, uuid4()]:
            with self.subTest(value=value):
                response = self.client.get(reverse('chat:index'), {'conversation': str(value)})
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, 'Mensaje privado ajeno', status_code=404)

    def test_anonymous_session_authorization_does_not_allow_owned_conversations(self):
        missing = uuid4()
        self.authorize_session(self.owned.pk, self.anonymous.pk, missing)
        for value in [self.owned.pk, self.unrelated_anonymous.pk, missing]:
            with self.subTest(value=value):
                response = self.client.get(reverse('chat:index'), {'conversation': str(value)})
                self.assertEqual(response.status_code, 404)

    def test_own_conversation_opens(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('chat:index'), {'conversation': str(self.owned.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['conversation'], self.owned)
        self.assertContains(response, 'Mensaje propio')

    def test_authorized_anonymous_conversation_opens_with_normalized_uuid(self):
        self.authorize_session(self.anonymous.pk)
        for value in [str(self.anonymous.pk), str(self.anonymous.pk).upper(), self.anonymous.pk.hex]:
            with self.subTest(value=value):
                response = self.client.get(reverse('chat:index'), {'conversation': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['conversation'], self.anonymous)

    @patch('applications.chat.views.answer_message')
    def test_invalid_or_inaccessible_send_returns_json_without_side_effects(self, answer):
        before_conversations = Conversation.objects.count()
        before_messages = Message.objects.count()
        for authenticated in [False, True]:
            if authenticated:
                self.client.force_login(self.user)
            values = ['hola', str(uuid4()), str(self.foreign.pk), str(self.unrelated_anonymous.pk)]
            for value in values:
                with self.subTest(authenticated=authenticated, value=value):
                    response = self.client.post(reverse('chat:send'), {
                        'conversation': value, 'message': 'Hola',
                    })
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {'error': 'La conversación indicada no es válida.'})
        self.assertEqual(Conversation.objects.count(), before_conversations)
        self.assertEqual(Message.objects.count(), before_messages)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.source, 'google_sheets')
        answer.assert_not_called()

    @patch('applications.chat.views.answer_message', return_value='Respuesta válida')
    def test_accessible_conversations_accept_messages(self, answer):
        self.authorize_session(self.anonymous.pk)
        response = self.client.post(reverse('chat:send'), {
            'conversation': str(self.anonymous.pk), 'message': 'Consulta anónima',
        })
        self.assertEqual(response.status_code, 200)
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {
            'conversation': str(self.owned.pk), 'message': 'Consulta propia',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(answer.call_count, 2)

    @patch('applications.chat.views.answer_message', return_value='Nueva conversación')
    def test_without_id_opens_chat_and_creates_new_conversation_on_send(self, answer):
        for authenticated in [False, True]:
            if authenticated:
                self.client.force_login(self.user)
            with self.subTest(authenticated=authenticated):
                response = self.client.get(reverse('chat:index'))
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context['conversation'])
                response = self.client.post(reverse('chat:send'), {'message': 'Hola'})
                self.assertEqual(response.status_code, 200)
                conversation = Conversation.objects.get(pk=response.json()['conversation'])
                self.assertEqual(conversation.user_id, self.user.pk if authenticated else None)


class SheetResponseSafetyTests(SimpleTestCase):
    def setUp(self):
        self.conversation = SimpleNamespace(provider='openrouter')
        self.products = [
            {'sku': 'VAL-01', 'name': 'Válvula de bronce', 'price': 25, 'stock': 8, 'unit': 'unidades'},
            {'sku': 'VAL-02', 'name': 'Válvula de acero', 'price': 40, 'stock': 15, 'unit': 'unidades'},
        ]
        self.search = patch('applications.chat.services.sheet_search_products', return_value=self.products[:1]).start()
        self.generate = patch('applications.chat.services.generate_response').start()
        self.addCleanup(patch.stopall)

    def test_no_matches_does_not_call_model(self):
        self.search.return_value = []
        self.assertIn('No encontré', answer_sheet_message(self.conversation, 'válvula'))
        self.generate.assert_not_called()

    def test_values_are_rendered_from_selected_product(self):
        self.generate.return_value = '{"productos":[{"sku":"VAL-01","campos":["precio","stock"]}]}'
        result = answer_sheet_message(self.conversation, 'válvula')
        self.assertIn('está a 25', result)
        self.assertIn('hay 8 unidades', result)
        self.assertNotIn('40', result)
        self.assertNotIn('VAL-02', result)

    def test_invalid_selections_use_safe_fallback(self):
        for raw in [
            'Precio inventado: 999',
            '{"productos":[{"sku":"VAL-99","campos":["precio"]}]}',
            '{"productos":[{"sku":"VAL-01","campos":["descuento"]}]}',
            '{"productos":[{"sku":"VAL-01","campos":["precio"],"precio":999}]}',
            '{"productos":[]}', 'null',
            '{"productos":[{"sku":[],"campos":["precio"]}]}',
        ]:
            with self.subTest(raw=raw):
                self.generate.return_value = raw
                result = answer_sheet_message(self.conversation, 'válvula')
                self.assertIn('está a 25', result)
                self.assertNotIn('está a 40', result)
                self.assertNotIn('999', result)

    def test_provider_failure_preserves_local_information(self):
        self.generate.side_effect = ProviderError('offline')
        self.assertIn('hay 8 unidades', answer_sheet_message(self.conversation, 'válvula'))

    def test_missing_values_and_zero_stock_are_distinct(self):
        self.products[0].update(price=None, stock=0)
        self.generate.return_value = '{}'
        result = answer_sheet_message(self.conversation, 'válvula')
        self.assertIn('no tengo el precio en el inventario', result)
        self.assertIn('está agotado', result)
        self.assertNotIn('no tengo el stock', result)

    def test_image_is_only_used_for_search_not_final_selection(self):
        self.generate.side_effect = ['válvula bronce', '{"productos":[{"sku":"VAL-01","campos":["precio"]}]}']
        result = answer_sheet_message(self.conversation, 'El usuario envió una imagen.', image_data_url='data:image/jpeg;base64,abc')
        self.assertIn('posibles coincidencias', result)
        self.assertIn('número de opción', result)
        self.generate.assert_called_once()

    def test_unrecognized_image_requests_keywords(self):
        self.generate.return_value = 'desconocido'
        result = answer_sheet_message(self.conversation, 'El usuario envió una imagen.', image_data_url='image')
        self.assertIn('entre 3 y 5 palabras clave', result)
        self.search.assert_not_called()

    @patch('applications.chat.services.sheet_product_by_sku', return_value=None)
    def test_unknown_explicit_sku_does_not_suggest_other_products(self, lookup):
        self.assertIn('No encontré', answer_sheet_message(self.conversation, 'SKU UNKNOWN-1'))
        self.generate.assert_not_called()
        self.search.assert_not_called()


class InventorySearchTests(TestCase):
    def product(self, sku, name, **kwargs):
        return Product.objects.create(sku=sku, name=name, **kwargs)

    def test_fraction_and_inch_variants_are_equivalent(self):
        for value in ['½', '1/2', 'media pulgada', '0.5 pulgadas', '1/2"']:
            with self.subTest(value=value):
                self.assertEqual(search_terms(value), {'dim_inch_1_2'})
        self.assertEqual(search_terms('1½ pulgadas'), search_terms('1 1/2 pulgadas'))

    def test_synonyms_find_catalog_products(self):
        self.product('VAL-01', 'Válvula de bronce 1/2 pulgada')
        self.product('COD-01', 'Codo inoxidable')
        for query, sku in [('Necesito una llave de paso de media pulgada', 'VAL-01'),
                           ('válvulas bronze ½', 'VAL-01'), ('elbow inox', 'COD-01')]:
            with self.subTest(query=query):
                self.assertEqual([item['sku'] for item in search_inventory(query)], [sku])

    def test_measure_and_connection_are_required_not_partial_matches(self):
        self.product('VAL-01', 'Válvula de bronce 1/2 pulgada NPT')
        self.product('VAL-02', 'Válvula de bronce 3/4 pulgada BSP')
        self.assertEqual([item['sku'] for item in search_inventory('válvula bronce media pulgada NPT')], ['VAL-01'])
        self.assertEqual(search_inventory('válvula bronce media pulgada BSP'), [])
        self.assertEqual(search_inventory('válvula bronce ZX999'), [])

    def test_mm_and_inches_are_not_equivalent(self):
        self.product('MM-15', 'Tubo 15 mm')
        self.product('IN-15', 'Tubo 15 pulgadas')
        self.assertEqual([item['sku'] for item in search_inventory('tubería 15 milímetros')], ['MM-15'])
        self.assertEqual([item['sku'] for item in search_inventory('tubo 15 pulgadas')], ['IN-15'])

    def test_exact_reference_wins_without_confusing_similar_skus(self):
        self.product('VAL-01', 'Válvula de bronce')
        self.product('VAL-010', 'Válvula de acero')
        self.product('ABC/2.5_X', 'Adaptador especial')
        self.assertEqual([item['sku'] for item in search_inventory('¿Cuánto cuesta val-01?')], ['VAL-01'])
        self.assertEqual([item['sku'] for item in search_inventory('ABC/2.5_X')], ['ABC/2.5_X'])
        self.assertEqual(search_inventory('VAL-019'), [])

    def test_inactive_products_are_excluded_and_specs_are_searchable(self):
        self.product('OFF', 'Válvula bronce', active=False)
        self.product('ON', 'Válvula', specs='Bronce, conexión NPT 1/2 pulgada')
        self.assertEqual([item['sku'] for item in search_inventory('válvula bronce NPT media pulgada')], ['ON'])
        self.assertEqual(search_inventory('OFF'), [])


class InventoryConversationTests(TestCase):
    def setUp(self):
        self.conversation = Conversation.objects.create()
        self.valve = Product.objects.create(sku='VAL-01', name='Válvula de bronce 1/2 pulgada', price='25.00', stock=8)
        self.other = Product.objects.create(sku='VAL-02', name='Válvula de bronce 3/4 pulgada', price='40.00', stock=15)
        self.generate = patch('applications.chat.services.generate_response', return_value='{}').start()
        self.addCleanup(patch.stopall)

    def ask(self, text, **kwargs):
        Message.objects.create(conversation=self.conversation, role=Message.USER, content=text)
        result = answer_sheet_message(self.conversation, text, **kwargs)
        Message.objects.create(conversation=self.conversation, role=Message.ASSISTANT, content=result,
                               inventory_context=getattr(result, 'inventory_context', {}))
        return result

    def test_follow_up_remembers_sku_but_refreshes_stock_and_price(self):
        self.ask('SKU VAL-01')
        Product.objects.filter(pk=self.valve.pk).update(price='27.00', stock=3)
        answer = self.ask('¿Y cuántas unidades hay?')
        self.assertIn('hay 3', answer)
        self.assertIn('está a 27', answer)
        self.assertNotIn('VAL-02', answer)

    def test_ambiguity_requires_customer_confirmation_without_ai_guessing(self):
        answer = self.ask('Busco una válvula de bronce')
        self.assertIn('varios productos', answer)
        self.assertIn('VAL-01', answer)
        self.assertIn('VAL-02', answer)
        self.assertNotIn('está a', answer)
        self.generate.assert_not_called()
        follow_up = self.ask('¿Cuántas unidades hay?')
        self.assertIn('número de opción', follow_up)
        self.assertNotIn('hay 8', follow_up)
        self.assertNotIn('hay 15', follow_up)

    def test_customer_can_choose_option_and_then_follow_up(self):
        self.ask('válvula bronce')
        selected = self.ask('la segunda')
        self.assertIn('VAL-02', selected)
        self.assertNotIn('VAL-01', selected)
        self.assertIn('hay 15', self.ask('¿Y el stock?'))

    def test_measure_can_disambiguate_previous_options(self):
        self.ask('válvula bronce')
        selected = self.ask('la de media pulgada')
        self.assertIn('VAL-01', selected)
        self.assertNotIn('VAL-02', selected)

    def test_refinement_does_not_switch_to_unrelated_product(self):
        Product.objects.create(sku='TUB-01', name='Tubo de una pulgada', specs='1 pulgada')
        self.ask('válvula bronce')
        result = self.ask('la de 1 pulgada')
        self.assertIn('No encontré', result)
        self.assertNotIn('TUB-01', result)

    def test_new_product_replaces_previous_context(self):
        Product.objects.create(sku='COD-01', name='Codo de acero', stock=2)
        self.ask('SKU VAL-01')
        self.ask('Busco un codo de acero')
        result = self.ask('¿Cuántas unidades hay?')
        self.assertIn('COD-01', result)
        self.assertIn('hay 2', result)
        self.assertNotIn('VAL-01', result)

    def test_unknown_reference_clears_previous_selection(self):
        self.ask('SKU VAL-01')
        self.assertIn('No encontré', self.ask('SKU UNKNOWN-1'))
        self.assertIn('No encontré', self.ask('¿Y su precio?'))

    def test_removed_or_inactive_selection_is_not_reused(self):
        self.ask('SKU VAL-01')
        Product.objects.filter(pk=self.valve.pk).update(active=False)
        self.assertIn('No encontré', self.ask('¿Y el stock?'))

    def test_other_conversation_context_is_never_used(self):
        self.ask('SKU VAL-01')
        self.conversation = Conversation.objects.create()
        result = self.ask('¿Cuántas unidades hay?')
        self.assertIn('No encontré', result)
        self.assertNotIn('VAL-01', result)

    def test_unknown_new_model_does_not_reuse_old_product(self):
        self.ask('SKU VAL-01')
        self.assertIn('No encontré', self.ask('¿Precio de la válvula ZX999?'))

    def test_image_match_requires_confirmation_even_for_single_product(self):
        self.generate.return_value = 'válvula bronce media pulgada'
        result = self.ask('El usuario envió una imagen.', image_data_url='image')
        self.assertIn('posibles coincidencias', result)
        self.assertNotIn('está a', result)
        self.generate.return_value = '{}'
        confirmed = self.ask('sí')
        self.assertIn('VAL-01', confirmed)
        self.assertIn('está a 25', confirmed)

    def test_web_endpoint_persists_trusted_context(self):
        first = self.client.post(reverse('chat:send'), {'message': 'SKU VAL-01'})
        self.assertEqual(first.status_code, 200)
        conversation = first.json()['conversation']
        second = self.client.post(reverse('chat:send'), {'message': '¿Y el stock?', 'conversation': conversation})
        self.assertEqual(second.status_code, 200)
        self.assertIn('hay 8', second.json()['response'])
        self.assertEqual(Conversation.objects.get(pk=conversation).messages.last().inventory_context, {'selected_sku': 'VAL-01'})


@override_settings(OPENROUTER_API_KEY='')
class ChatFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('chat@test.com', 'Passw0rd!123', first_name='Chat', last_name='User')

    def test_chat_is_visible_without_authentication(self):
        response = self.client.get(reverse('chat:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escribe tu pregunta')
        self.assertContains(response, 'inventario')

    @patch('applications.chat.views.answer_message', return_value='Respuesta pública.')
    def test_anonymous_user_can_send_messages(self, answer):
        response = self.client.post(reverse('chat:send'), {'message': 'Hola'})
        self.assertEqual(response.status_code, 200)
        conversation = Conversation.objects.get(user__isnull=True)
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(response.json()['response'], 'Respuesta pública.')
        answer.assert_called_once()

    def test_empty_message_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {'message': '  '})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Conversation.objects.count(), 0)

    @patch('applications.chat.views.answer_message', side_effect=RuntimeError)
    def test_provider_unexpected_error_does_not_leave_messages(self, answer):
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {'message': 'Hola'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Message.objects.count(), 0)

    def test_invalid_conversation_id_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {
            'message': 'Hola',
            'conversation': 'not-a-uuid',
        })
        self.assertEqual(response.status_code, 404)

    def test_web_chat_rejects_oversized_messages(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {'message': 'x' * 4001})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Conversation.objects.count(), 0)
