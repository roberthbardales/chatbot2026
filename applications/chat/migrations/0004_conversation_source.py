from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0003_default_groq_provider')]
    operations = [migrations.AddField(
        model_name='conversation', name='source',
        field=models.CharField(choices=[('blog', 'Artículos del blog'), ('dummyjson', 'Productos')], default='blog', max_length=20),
    )]
