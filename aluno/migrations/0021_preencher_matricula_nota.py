from django.db import migrations


def preencher_matricula(apps, schema_editor):
    Turma = apps.get_model('aluno', 'Turma')
    Matricula = apps.get_model('aluno', 'Matricula')
    Nota = apps.get_model('aluno', 'Nota')

    # Quem estava só em Turma.alunos (sem Matricula) ganha a Matricula,
    # porque a relação aluno-turma passa a existir apenas por ela.
    for turma in Turma.objects.all():
        for aluno in turma.alunos.all():
            if not Matricula.objects.filter(aluno_id=aluno.id, turma_id=turma.id).exists():
                Matricula.objects.create(aluno_id=aluno.id, turma_id=turma.id, ativo=True)

    for nota in Nota.objects.filter(turma__isnull=False):
        matricula = Matricula.objects.filter(
            aluno_id=nota.aluno_id, turma_id=nota.turma_id
        ).order_by('id').first()
        if matricula:
            nota.matricula = matricula
            nota.save(update_fields=['matricula'])

    # Nota sem matrícula não existe no modelo (nroSeq é NOT NULL).
    Nota.objects.filter(matricula__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0020_nota_matricula_schema'),
    ]

    operations = [
        migrations.RunPython(preencher_matricula, migrations.RunPython.noop),
    ]
