from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0004_conversation_source')]

    operations = [
        migrations.AlterField(
            model_name='conversation',
            name='source',
            field=models.CharField(
                choices=[('blog', 'Artículos del blog'), ('dummyjson', 'Productos')],
                default='dummyjson',
                max_length=20,
            ),
        ),
    ]
