from django.db import migrations, models
import pgvector.django


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0005_default_dummyjson_source'),
    ]

    operations = [
        pgvector.django.VectorExtension(),
        migrations.CreateModel(
            name='ProductDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dummyjson_id', models.PositiveIntegerField(unique=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('category', models.CharField(blank=True, max_length=120)),
                ('brand', models.CharField(blank=True, max_length=120)),
                ('sku', models.CharField(blank=True, max_length=120)),
                ('price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('stock', models.IntegerField(blank=True, null=True)),
                ('content', models.TextField()),
                ('content_hash', models.CharField(max_length=64)),
                ('embedding', pgvector.django.VectorField(dimensions=768)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ('dummyjson_id',)},
        ),
    ]
