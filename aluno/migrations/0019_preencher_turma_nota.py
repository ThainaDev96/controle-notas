from django.db import migrations


def preencher_turma(apps, schema_editor):
    Nota = apps.get_model('aluno', 'Nota')
    Turma = apps.get_model('aluno', 'Turma')

    # Uma Nota por (aluno, disciplina) passa a apontar para uma das turmas
    # do aluno naquela disciplina (a de menor id quando houver mais de uma).
    # Notas sem turma correspondente ficam com turma nula, sem serem apagadas.
    for nota in Nota.objects.filter(turma__isnull=True):
        turma = Turma.objects.filter(
            alunos=nota.aluno_id, disciplina_id=nota.disciplina_id
        ).order_by('id').first()
        if turma:
            nota.turma = turma
            nota.save(update_fields=['turma'])


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0018_nota_turma'),
    ]

    operations = [
        migrations.RunPython(preencher_turma, migrations.RunPython.noop),
    ]
