from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0023_matricula_turma_verbose'),
    ]

    operations = [
        # A cópia de notas para mais de uma Nota do mesmo aluno exige soltar a
        # unicidade antiga (aluno, avaliacao) antes de migrar os dados.
        migrations.AlterUniqueTogether(name='notaavaliacao', unique_together=set()),
        migrations.AddField(
            model_name='notaavaliacao',
            name='resumo',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='aluno.nota', verbose_name='Nota'),
        ),
    ]
