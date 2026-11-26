from django.db import migrations


def voltar_situacao_para_as_notas(apps, schema_editor):
    """
    O diário de classe saiu do sistema, e a reprovação por falta junto. Quem estava reprovado e tinha
    falta lançada volta a ter a situação que sai das notas e do exame. Nenhuma nota é alterada.
    Banco sem falta lançada (como o de teste) não faz nada.
    """
    Falta = apps.get_model('aluno', 'Falta')
    Nota = apps.get_model('aluno', 'Nota')
    com_falta = set(Falta.objects.filter(aula__realizada=True).values_list('matricula_id', flat=True))
    if not com_falta:
        return
    reprovadas = set(
        Nota.objects.filter(situacao='reprovado', matricula_id__in=com_falta).values_list('matricula_id', flat=True)
    )
    if not reprovadas:
        return
    # O cálculo é o mesmo das telas de notas.
    from aluno.models import Matricula
    from aluno.views import _recalcular_media_disciplina
    for matricula in Matricula.objects.filter(id__in=reprovadas).select_related('turma__disciplina'):
        _recalcular_media_disciplina(matricula)


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0030_falta_justificada'),
    ]

    operations = [
        migrations.RunPython(voltar_situacao_para_as_notas, migrations.RunPython.noop),
        migrations.DeleteModel(name='Falta'),
        migrations.DeleteModel(name='Aula'),
        migrations.RemoveField(model_name='disciplina', name='total_periodos'),
    ]
