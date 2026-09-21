from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0021_preencher_matricula_nota'),
    ]

    operations = [
        migrations.AlterUniqueTogether(name='nota', unique_together=set()),
        migrations.AlterField(
            model_name='nota',
            name='matricula',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='aluno.matricula', verbose_name='Matrícula'),
        ),
        migrations.RemoveField(model_name='nota', name='aluno'),
        migrations.RemoveField(model_name='nota', name='disciplina'),
        migrations.RemoveField(model_name='nota', name='turma'),
        migrations.RemoveField(model_name='nota', name='ativo'),
        migrations.RemoveField(model_name='turma', name='alunos'),
        migrations.RemoveField(model_name='avaliacao', name='ano'),
    ]
