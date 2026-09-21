from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0025_preencher_resumo_notaavaliacao'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notaavaliacao',
            name='resumo',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='aluno.nota', verbose_name='Nota'),
        ),
        migrations.RemoveField(model_name='notaavaliacao', name='aluno'),
        migrations.AlterUniqueTogether(name='notaavaliacao', unique_together={('resumo', 'avaliacao')}),
    ]
