from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('chat', '0006_productdocument'),
    ]

    operations = [
        migrations.AlterField(
            model_name='conversation',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.CASCADE,
                related_name='chat_conversations',
                to='users.user',
            ),
        ),
    ]
