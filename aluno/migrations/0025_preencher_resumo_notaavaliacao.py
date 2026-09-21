from django.db import migrations


def preencher_resumo(apps, schema_editor):
    Nota = apps.get_model('aluno', 'Nota')
    Matricula = apps.get_model('aluno', 'Matricula')
    NotaAvaliacao = apps.get_model('aluno', 'NotaAvaliacao')

    for na_id in list(NotaAvaliacao.objects.values_list('id', flat=True)):
        na = NotaAvaliacao.objects.select_related('avaliacao').get(id=na_id)
        disciplina_id = na.avaliacao.disciplina_id

        notas = list(Nota.objects.filter(
            matricula__aluno_id=na.aluno_id, matricula__turma__disciplina_id=disciplina_id
        ).order_by('id'))

        if not notas:
            matricula = Matricula.objects.filter(
                aluno_id=na.aluno_id, turma__disciplina_id=disciplina_id, ativo=True
            ).order_by('id').first()
            if matricula is None:
                na.delete()  # sem matrícula na disciplina, não há Nota a que ligar
                continue
            notas = [Nota.objects.create(matricula=matricula, situacao='cursando')]

        # Cada Nota candidata fica com a sua cópia, para nenhuma perder o que exibia.
        na.resumo = notas[0]
        na.save(update_fields=['resumo'])
        for nota in notas[1:]:
            NotaAvaliacao.objects.create(
                aluno_id=na.aluno_id, avaliacao_id=na.avaliacao_id, nota=na.nota, resumo=nota
            )


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0024_notaavaliacao_resumo_schema'),
    ]

    operations = [
        migrations.RunPython(preencher_resumo, migrations.RunPython.noop),
    ]
