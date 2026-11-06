import core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comms', '0016_discussion_effacee'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='file_duration',
            field=models.PositiveIntegerField(blank=True, help_text="Durée d'une vidéo, en secondes.", null=True),
        ),
        migrations.AddField(
            model_name='message',
            name='file_thumbnail',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='chat_files/vignettes/',
                validators=[core.validators.ValidateUploadSize()],
            ),
        ),
        migrations.AlterField(
            model_name='message',
            name='file',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='chat_files/',
                validators=[
                    core.validators.ValidatePieceJointeSize(),
                    core.validators.validate_extension_piece_jointe,
                ],
            ),
        ),
    ]
