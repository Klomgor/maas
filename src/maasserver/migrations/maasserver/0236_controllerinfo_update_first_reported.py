# Generated by Django 2.2.12 on 2021-04-16 07:29

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maasserver", "0235_controllerinfo_versions_details"),
    ]

    operations = [
        migrations.AddField(
            model_name="controllerinfo",
            name="update_first_reported",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
