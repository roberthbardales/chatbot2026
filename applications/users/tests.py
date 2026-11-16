from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


TEST_SETTINGS = override_settings(SECURE_SSL_REDIRECT=False)


@TEST_SETTINGS
class UserRegisterViewTests(TestCase):

    def test_register_creates_user(self):
        data = {
            'email': 'usuario@test.com',
            'first_name': 'Juan',
            'last_name': 'Pérez',
            'gender': 'M',
            'phone': '999888777',
            'password1': 'Passw0rd!123',
            'password2': 'Passw0rd!123',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertRedirects(response, reverse('app_users:login'))
        self.assertTrue(User.objects.filter(email='usuario@test.com').exists())
        user = User.objects.get(email='usuario@test.com')
        self.assertEqual(user.first_name, 'Juan')
        self.assertEqual(user.occupation, '1')

    def test_register_duplicate_email_rejected(self):
        User.objects.create_user('dup@test.com', 'Passw0rd!123', first_name='Ana', last_name='Luz')
        data = {
            'email': 'dup@test.com',
            'first_name': 'Otro',
            'last_name': 'User',
            'gender': 'M',
            'password1': 'Passw0rd!123',
            'password2': 'Passw0rd!123',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ya existe un usuario con este correo electrónico.')

    def test_register_password_mismatch_rejected(self):
        data = {
            'email': 'x@test.com',
            'first_name': 'A',
            'last_name': 'B',
            'gender': 'M',
            'password1': 'Passw0rd!123',
            'password2': 'OtraPass0rd!456',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las contraseñas no coinciden.')

    def test_register_ignores_requested_occupation(self):
        """Aunque el cliente envíe una ocupación (campo ya inexistente), la cuenta siempre es de usuario normal."""
        data = {
            'email': 'admin-attempt@test.com',
            'first_name': 'Atacante',
            'last_name': 'Test',
            'occupation': User.ADMINISTRADOR,
            'gender': 'M',
            'password1': 'Passw0rd!123',
            'password2': 'Passw0rd!123',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertRedirects(response, reverse('app_users:login'))
        user = User.objects.get(email=data['email'])
        self.assertEqual(user.occupation, User.USUARIO)
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_register_form_has_no_occupation_field(self):
        response = self.client.get(reverse('app_users:register'))
        self.assertNotIn('occupation', response.context['form'].fields)
        self.assertNotContains(response, 'Ocupación')

    @override_settings(AUTH_REGISTER_PER_IP=2)
    def test_register_is_rate_limited_per_ip(self):
        def register(n):
            return self.client.post(reverse('app_users:register'), {
                'email': f'masivo{n}@test.com', 'first_name': 'A', 'last_name': 'B', 'gender': 'M',
                'password1': 'Passw0rd!123', 'password2': 'Passw0rd!123',
            })
        self.assertEqual(register(1).status_code, 302)
        self.assertEqual(register(2).status_code, 302)
        blocked = register(3)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('Retry-After', blocked)
        self.assertFalse(User.objects.filter(email='masivo3@test.com').exists())


@TEST_SETTINGS
class LoginUserTests(TestCase):

    def setUp(self):
        self.password = 'Passw0rd!123'
        self.user = User.objects.create_user('login@test.com', self.password, first_name='Carlos', last_name='Ruiz')

    def test_login_success(self):
        response = self.client.post(reverse('app_users:login'), {
            'email': 'login@test.com',
            'password': self.password,
        })
        self.assertRedirects(response, reverse('app_users:dashboard'))

    def test_login_wrong_password(self):
        response = self.client.post(reverse('app_users:login'), {
            'email': 'login@test.com',
            'password': 'incorrecta',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email o contraseña incorrectos.')

    def test_login_unknown_email(self):
        response = self.client.post(reverse('app_users:login'), {
            'email': 'nobody@test.com',
            'password': 'Passw0rd!123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email o contraseña incorrectos.')


    def test_login_returns_to_requested_internal_page(self):
        response = self.client.post(reverse('app_users:login') + '?next=/whatsapp/',
                                    {'email': 'login@test.com', 'password': self.password})
        self.assertRedirects(response, '/whatsapp/', fetch_redirect_response=False)

    def test_login_ignores_external_or_malicious_next(self):
        for destination in ['https://sitio-falso.com/', '//sitio-falso.com/', 'javascript:alert(1)', r'/\sitio-falso.com']:
            with self.subTest(destination=destination):
                self.client.logout()
                response = self.client.post(reverse('app_users:login') + '?next=' + destination,
                                            {'email': 'login@test.com', 'password': self.password})
                self.assertRedirects(response, reverse('app_users:dashboard'), fetch_redirect_response=False)

    def test_protected_page_sends_user_to_login_keeping_next(self):
        response = self.client.get(reverse('app_users:lista'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])

    @override_settings(AUTH_LOGIN_PER_EMAIL=3)
    def test_login_is_rate_limited_per_email_even_with_correct_password(self):
        for _ in range(3):
            response = self.client.post(reverse('app_users:login'), {'email': 'login@test.com', 'password': 'mala'})
            self.assertEqual(response.status_code, 200)
        blocked = self.client.post(reverse('app_users:login'), {'email': 'LOGIN@test.com', 'password': 'Passw0rd!123'})
        self.assertEqual(blocked.status_code, 429)
        self.assertContains(blocked, 'Demasiados intentos', status_code=429)
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(AUTH_LOGIN_PER_EMAIL=3)
    def test_other_emails_are_not_blocked_by_one_accounts_attempts(self):
        for _ in range(4):
            self.client.post(reverse('app_users:login'), {'email': 'login@test.com', 'password': 'mala'})
        response = self.client.post(reverse('app_users:login'), {'email': 'otro@test.com', 'password': 'mala'})
        self.assertEqual(response.status_code, 200)


@TEST_SETTINGS
class UpdatePasswordViewTests(TestCase):

    def setUp(self):
        self.password = 'Passw0rd!123'
        self.user = User.objects.create_user('pass@test.com', self.password, first_name='Ana', last_name='Lira')
        self.client.login(email='pass@test.com', password=self.password)

    def test_change_password_success(self):
        response = self.client.post(reverse('app_users:user-update'), {
            'password1': self.password,
            'password2': 'NuevaPass0rd!456',
            'password3': 'NuevaPass0rd!456',
        })
        self.assertRedirects(response, reverse('app_users:login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NuevaPass0rd!456'))

    def test_change_password_wrong_current(self):
        response = self.client.post(reverse('app_users:user-update'), {
            'password1': 'incorrecta',
            'password2': 'NuevaPass0rd!456',
            'password3': 'NuevaPass0rd!456',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La contraseña actual es incorrecta.')

    def test_change_password_mismatch(self):
        response = self.client.post(reverse('app_users:user-update'), {
            'password1': self.password,
            'password2': 'NuevaPass0rd!456',
            'password3': 'OtraPass0rd!789',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las contraseñas no coinciden.')


@TEST_SETTINGS
class ListaUsuariosViewTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            'admin@test.com', 'Passw0rd!123',
            first_name='Admin', last_name='Root', occupation='0',
        )
        self.usuario = User.objects.create_user(
            'usuario@test.com', 'Passw0rd!123',
            first_name='Pa', last_name='Ciente', occupation='1',
        )

    def test_admin_can_list_users(self):
        self.client.login(email='admin@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:lista'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'usuario@test.com')
        self.assertQuerySetEqual(
            response.context['usuarios'],
            list(User.objects.filter(pk__in=[self.admin.pk, self.usuario.pk])),
            ordered=False,
        )

    def test_non_admin_forbidden(self):
        self.client.login(email='usuario@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:lista'))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('app_users:lista'))
        self.assertRedirects(response, reverse('app_users:login') + '?next=/users/lista/')


@TEST_SETTINGS
class LogoutViewTests(TestCase):

    def test_logout_requires_post(self):
        user = User.objects.create_user('out@test.com', 'Passw0rd!123', first_name='X', last_name='Y')
        self.client.login(email='out@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:logout'))
        self.assertEqual(response.status_code, 405)

    def test_logout_post_redirects_to_login(self):
        user = User.objects.create_user('out@test.com', 'Passw0rd!123', first_name='X', last_name='Y')
        self.client.login(email='out@test.com', password='Passw0rd!123')
        response = self.client.post(reverse('app_users:logout'))
        self.assertRedirects(response, reverse('app_users:login'))
