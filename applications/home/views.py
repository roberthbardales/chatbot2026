from django.views.generic import TemplateView


class IndexView(TemplateView):
    template_name = 'home/index.html'


class PrivacyView(TemplateView):
    template_name = 'home/privacidad.html'
