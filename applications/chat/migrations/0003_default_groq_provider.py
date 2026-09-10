from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0002_provider_fields')]

    operations = [
        migrations.AlterField(
            model_name='conversation',
            name='provider',
            field=models.CharField(
                choices=[('gemini', 'Gemini 2.5 Flash-Lite'), ('groq', 'Groq GPT-OSS 20B')],
                default='groq',
                max_length=20,
            ),
        ),
    ]
