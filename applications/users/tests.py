from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class UserRegisterViewTests(TestCase):

    def test_register_creates_user(self):
        data = {
            'email': 'usuario@test.com',
            'first_name': 'Juan',
            'last_name': 'Pérez',
            'occupation': '1',
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
            'occupation': '1',
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
            'occupation': '1',
            'gender': 'M',
            'password1': 'Passw0rd!123',
            'password2': 'OtraPass0rd!456',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las contraseñas no coinciden.')


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
        self.assertQuerysetEqual(
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


class LogoutViewTests(TestCase):

    def test_logout_redirects_to_login(self):
        user = User.objects.create_user('out@test.com', 'Passw0rd!123', first_name='X', last_name='Y')
        self.client.login(email='out@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:logout'))
        self.assertRedirects(response, reverse('app_users:login'))