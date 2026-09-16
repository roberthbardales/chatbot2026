from unittest.mock import patch

from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from applications.users.models import User

from .blog_api import clean_content, latest_entries, search_entries
from .dummyjson_api import DummyJSONError, _products, product_by_sku
from .analytics import most_active_author, oldest_entry
from .models import Conversation, Message
from .services import answer_message


class BlogServiceTests(TestCase):
    def test_clean_content_removes_markup_and_limits_size(self):
        result = clean_content('<p>Hola&nbsp;mundo</p><script>secret()</script>', max_chars=20)
        self.assertEqual(result, 'Hola mundo')

    @patch('applications.chat.blog_api.list_entries')
    def test_search_entries_scores_title_matches(self, list_entries):
        list_entries.return_value = [
            {'id': 1, 'title': 'GitHub básico', 'resume': '', 'category': {}, 'tag': []},
            {'id': 2, 'title': 'Django', 'resume': 'Uso general', 'category': {}, 'tag': []},
        ]
        self.assertEqual(search_entries('GitHub')[0]['id'], 1)

    @patch('applications.chat.blog_api.list_entries')
    def test_latest_entries_orders_by_created_date(self, list_entries):
        list_entries.return_value = [
            {'id': 106, 'created': '2023-01-04T10:00:00Z'},
            {'id': 151, 'created': '2026-03-17T10:00:00Z'},
        ]
        self.assertEqual(latest_entries()[0]['id'], 151)

    def test_analytics_finds_oldest_entry_and_top_author(self):
        entries = [
            {'id': 1, 'created': '2022-01-04T10:00:00Z', 'user': {'full_name': 'Ana'}},
            {'id': 2, 'created': '2023-01-04T10:00:00Z', 'user': {'full_name': 'Ana'}},
            {'id': 3, 'created': '2024-01-04T10:00:00Z', 'user': {'full_name': 'Luis'}},
        ]
        self.assertEqual(oldest_entry(entries)['id'], 1)
        self.assertEqual(most_active_author(entries), ('Ana', 2))


@override_settings(OPENROUTER_API_KEY='')
class ChatFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('chat@test.com', 'Passw0rd!123', first_name='Chat', last_name='User')

    def test_chat_is_visible_without_authentication(self):
        response = self.client.get(reverse('chat:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escribe tu pregunta')
        self.assertContains(response, 'Productos DummyJSON')
        self.assertContains(response, 'Inventario')

    @patch('applications.chat.views.answer_message', return_value='Respuesta DummyJSON.')
    def test_dummyjson_source_can_be_selected(self, answer):
        response = self.client.post(reverse('chat:send'), {
            'message': 'Busca un cargador',
            'source': 'dummyjson',
            'provider': 'openrouter',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Conversation.objects.get().source, 'dummyjson')
        answer.assert_called_once()

    @patch('applications.chat.views.answer_message', return_value='Respuesta pública.')
    def test_anonymous_user_can_send_messages(self, answer):
        response = self.client.post(reverse('chat:send'), {'message': 'Hola'})
        self.assertEqual(response.status_code, 200)
        conversation = Conversation.objects.get(user__isnull=True)
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(response.json()['response'], 'Respuesta pública.')
        answer.assert_called_once()

    @patch('applications.chat.services.generate_response', return_value='Respuesta basada en el artículo.')
    @override_settings(BLOG_SOURCE_ENABLED=True)
    @patch('applications.chat.services.search_entries')
    def test_send_creates_conversation_and_messages(self, search, generate):
        search.return_value = [{'id': 1, 'title': 'GitHub', 'resume': 'Guía', 'content': '<p>Contenido</p>', 'slug': 'github', 'category': {}, 'user': {}}]
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {'message': 'Háblame de GitHub', 'source': 'blog'})
        self.assertEqual(response.status_code, 200)
        conversation = Conversation.objects.get(user=self.user)
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(conversation.messages.first().role, Message.USER)
        self.assertEqual(conversation.provider, 'openrouter')
        self.assertEqual(conversation.messages.last().provider, 'openrouter')
        self.assertEqual(response.json()['response'], 'Respuesta basada en el artículo.')

    @patch('applications.chat.services.generate_response', return_value='Respuesta de Groq.')
    @override_settings(BLOG_SOURCE_ENABLED=True)
    @patch('applications.chat.services.search_entries', return_value=[])
    def test_selected_provider_is_persisted(self, search, generate):
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {'message': 'Hola', 'provider': 'openrouter', 'source': 'blog'})
        self.assertEqual(response.status_code, 200)
        conversation = Conversation.objects.get(user=self.user)
        self.assertEqual(conversation.provider, 'openrouter')
        generate.assert_called_once()

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

    @patch('applications.chat.services.product_by_id')
    def test_products_can_be_compared_by_id(self, product_by_id):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_id.side_effect = [
            {'id': 1, 'title': 'Producto A', 'price': 10, 'stock': 4},
            {'id': 2, 'title': 'Producto B', 'price': 20, 'stock': 8},
        ]
        response = answer_message(conversation, 'Compara los productos 1 y 2')
        self.assertIn('Producto A cuesta 10', response)
        self.assertIn('Producto B cuesta 20', response)

    @override_settings(OPENROUTER_API_KEY='configured')
    @patch('applications.chat.services.generate_response', return_value='Producto seguro cuesta 8 y tiene 6 unidades en stock.')
    @patch('applications.chat.services.product_by_sku')
    def test_dummyjson_natural_response_receives_verified_context(self, product_by_sku, generate):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_sku.return_value = {
            'title': 'Producto seguro', 'sku': 'REAL-1', 'price': 8,
            'stock': 6, 'description': 'Descripción verificada',
        }
        response = answer_message(conversation, 'SKU REAL-1')
        self.assertEqual(response, 'Producto seguro cuesta 8 y tiene 6 unidades en stock.')
        prompt = generate.call_args.args[1]
        self.assertIn('Producto seguro', prompt)
        self.assertIn('REAL-1', prompt)
        self.assertIn('8', prompt)
        self.assertIn('No inventes ni completes', prompt)

    @override_settings(OPENROUTER_API_KEY='configured')
    @patch('applications.chat.services.generate_response', return_value='Producto seguro cuesta 999 y tiene 6 unidades en stock.')
    @patch('applications.chat.services.product_by_sku')
    def test_dummyjson_rejects_unverified_numbers_from_model(self, product_by_sku, generate):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_sku.return_value = {'title': 'Producto seguro', 'sku': 'REAL-1', 'price': 8, 'stock': 6}
        response = answer_message(conversation, 'SKU REAL-1')
        self.assertIn('precio 8', response)
        self.assertNotIn('999', response)

    @patch('applications.chat.services.search_products')
    def test_product_question_removes_connector_words(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.return_value = [{
            'title': 'Apple iPhone Charger', 'sku': 'MOB-APP-APP-104',
            'price': 19.99, 'stock': 31,
        }]
        response = answer_message(conversation, '¿Cuál es el precio y stock de Apple iPhone Charger?')
        search_products.assert_called_once_with('Apple iPhone Charger', 6)
        self.assertIn('19.99', response)
        self.assertIn('31', response)

    @patch('applications.chat.services.search_products')
    def test_product_matching_is_case_and_accent_insensitive(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.return_value = [{'title': 'Cámara Digital', 'sku': 'CAM-1', 'price': 10, 'stock': 2}]
        response = answer_message(conversation, 'camara digital')
        self.assertIn('Cámara Digital', response)

    @patch('applications.chat.services.search_products')
    def test_product_partial_and_minor_typo_match(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.return_value = [{'title': 'Apple iPhone Charger', 'sku': 'MOB-1', 'price': 20, 'stock': 3}]
        self.assertIn('Apple iPhone Charger', answer_message(conversation, 'Apple iPhone Chager'))

    @patch('applications.chat.services.search_products')
    def test_product_ambiguous_and_nonexistent_are_rejected(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.return_value = [
            {'title': 'Apple iPhone Charger', 'price': 20, 'stock': 3},
            {'title': 'Apple iPhone Charger Fast', 'price': 25, 'stock': 4},
        ]
        self.assertIn('varios productos', answer_message(conversation, 'Apple iPhone'))
        search_products.return_value = [{'title': 'Completely Different', 'price': 1, 'stock': 1}]
        self.assertEqual(answer_message(conversation, 'Apple iPhone Charger'), 'No encontré ese producto en el inventario disponible.')

    @patch('applications.chat.services.product_by_sku')
    def test_sku_is_exact_and_never_generated(self, product_by_sku):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_sku.return_value = {'title': 'Producto', 'sku': 'REAL-1', 'price': 8, 'stock': 6}
        response = answer_message(conversation, 'SKU REAL-1')
        self.assertIn('REAL-1', response)
        self.assertNotIn('MOB-', response)

    @patch('applications.chat.services.product_by_sku', return_value=None)
    def test_missing_sku_is_not_invented(self, product_by_sku):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        self.assertEqual(answer_message(conversation, 'SKU UNKNOWN-1'), 'No encontré ese producto en el inventario disponible.')

    @patch('applications.chat.services.product_by_sku')
    def test_missing_product_fields_are_reported(self, product_by_sku):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_sku.return_value = {'title': 'Producto', 'sku': 'REAL-1'}
        response = answer_message(conversation, 'SKU REAL-1')
        self.assertIn('no proporcionado por DummyJSON', response)

    @patch('applications.chat.services.product_by_id')
    def test_comparison_reports_cheapest_and_highest_stock(self, product_by_id):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_id.side_effect = [
            {'id': 1, 'title': 'Producto A', 'price': 10, 'stock': 4},
            {'id': 2, 'title': 'Producto B', 'price': 20, 'stock': 8},
        ]
        response = answer_message(conversation, 'Compara los productos 1 y 2 e indica cuál es más económico y cuál tiene mayor stock.')
        self.assertIn('El más económico es Producto A (10)', response)
        self.assertIn('El de mayor stock es Producto B (8 unidades)', response)

    @patch('applications.chat.services.search_products')
    def test_products_can_be_compared_by_name(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.side_effect = [
            [{'title': 'Powder Canister', 'price': 14.99, 'stock': 89, 'sku': 'A'}],
            [{'title': 'Red Lipstick', 'price': 12.99, 'stock': 91, 'sku': 'B'}],
        ]
        response = answer_message(conversation, 'Compara Powder Canister y Red Lipstick')
        self.assertIn('El más económico es Red Lipstick (12.99)', response)
        self.assertIn('El de mayor stock es Red Lipstick (91 unidades)', response)

    @patch('applications.chat.services.product_by_id')
    def test_comparison_supports_three_product_ids(self, product_by_id):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_id.side_effect = [
            {'id': 6, 'title': 'Producto 6', 'price': 49.99, 'stock': 29},
            {'id': 7, 'title': 'Producto 7', 'price': 129.99, 'stock': 58},
            {'id': 8, 'title': 'Producto 8', 'price': 89.99, 'stock': 98},
        ]
        response = answer_message(conversation, 'Compara los productos 6, 7 y 8')
        self.assertIn('Producto 8 cuesta 89.99', response)
        self.assertIn('El de mayor stock es Producto 8 (98 unidades)', response)

    @patch('applications.chat.services.product_by_id')
    def test_comparison_with_missing_product_is_rejected(self, product_by_id):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        product_by_id.side_effect = [
            {'id': 1, 'title': 'Producto A', 'price': 10, 'stock': 4},
            None,
        ]
        response = answer_message(conversation, 'Compara los productos 1 y 999')
        self.assertEqual(response, 'No encontré ese producto en el inventario disponible.')

    @patch('applications.chat.services.search_products')
    def test_comparison_list_with_trailing_instruction(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.side_effect = [
            [{'title': "Dior J'adore", 'price': 89.99, 'stock': 98, 'sku': 'A'}],
            [{'title': 'Chanel Coco Noir Eau De', 'price': 129.99, 'stock': 58, 'sku': 'B'}],
            [{'title': 'Calvin Klein CK One', 'price': 49.99, 'stock': 29, 'sku': 'C'}],
        ]
        response = answer_message(conversation, 'Entre Dior J adore, Chanel Coco Noir Eau De y Calvin Klein CK One, compara precio y stock. Indica cuál es más económico y cuál tiene mayor disponibilidad.')
        self.assertIn('El más económico es Calvin Klein CK One (49.99)', response)
        self.assertIn("El de mayor stock es Dior J'adore (98 unidades)", response)

    @patch('applications.chat.services.search_products')
    def test_product_query_ignores_extra_requested_fields(self, search_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        search_products.return_value = [{'title': 'Apple iPhone Charger', 'sku': 'MOB-104', 'price': 19.99, 'stock': 31}]
        response = answer_message(conversation, '¿Qué precio, stock y SKU tiene Apple iPhone Charger? También dime si está disponible.')
        search_products.assert_called_once_with('Apple iPhone Charger', 6)
        self.assertIn('19.99', response)

    @patch('applications.chat.services.category_products')
    def test_category_query_extracts_only_category(self, category_products):
        conversation = Conversation.objects.create(user=self.user, source='dummyjson')
        category_products.return_value = [{'title': 'Producto', 'sku': 'A-1', 'price': 5, 'stock': 2}]
        answer_message(conversation, 'Dentro de la categoría belleza, muéstrame productos con precio, stock y SKU.')
        category_products.assert_called_once_with('belleza', 6)

    def test_web_chat_rejects_oversized_messages(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('chat:send'), {'message': 'x' * 4001})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Conversation.objects.count(), 0)

    @patch('applications.chat.dummyjson_api._request_json')
    def test_dummyjson_invalid_response_and_api_errors(self, request):
        request.return_value = {'unexpected': []}
        with self.assertRaises(DummyJSONError):
            _products(request.return_value)
        request.side_effect = DummyJSONError
        with self.assertRaises(DummyJSONError):
            product_by_sku('UNKNOWN')
