from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0019_preencher_turma_nota'),
    ]

    operations = [
        migrations.AddField(
            model_name='nota',
            name='matricula',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='aluno.matricula', verbose_name='Matrícula'),
        ),
        migrations.AlterField(
            model_name='turma',
            name='ano',
            field=models.PositiveIntegerField(verbose_name='Ano'),
        ),
    ]
