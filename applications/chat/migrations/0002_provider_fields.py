from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='provider',
            field=models.CharField(choices=[('gemini', 'Gemini 2.5 Flash-Lite'), ('groq', 'Groq GPT-OSS 20B')], default='gemini', max_length=20),
        ),
        migrations.AddField(
            model_name='message',
            name='provider',
            field=models.CharField(blank=True, choices=[('gemini', 'Gemini 2.5 Flash-Lite'), ('groq', 'Groq GPT-OSS 20B')], max_length=20, null=True),
        ),
    ]
