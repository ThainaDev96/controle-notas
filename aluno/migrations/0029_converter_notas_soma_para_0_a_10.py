from django.db import migrations


def _registros_de_soma(NotaAvaliacao):
    return list(NotaAvaliacao.objects.filter(
        avaliacao__disciplina__modo_calculo='soma',
        avaliacao__valor__gt=0,
        nota_obtida__isnull=False,
    ).select_related('avaliacao'))


def converter_para_0_a_10(apps, schema_editor):
    """
    Nas disciplinas de soma dos pesos a nota ia de 0 até o peso da avaliação; agora vai de 0 a 10 e vale
    proporcionalmente ao peso. Converter mantém a média de cada aluno: 2 de peso 4 vira 5, e 5 x 4 / 10 = 2.
    """
    NotaAvaliacao = apps.get_model('aluno', 'NotaAvaliacao')
    registros = _registros_de_soma(NotaAvaliacao)
    for registro in registros:
        # Nota acima do peso só pode ter sido digitada já na escala nova.
        if registro.nota_obtida <= registro.avaliacao.valor:
            # Duas casas: com uma, a média de alguns alunos mudaria no próximo recálculo.
            registro.nota_obtida = round(registro.nota_obtida * 10 / registro.avaliacao.valor, 2)
    NotaAvaliacao.objects.bulk_update(registros, ['nota_obtida'], batch_size=500)


def voltar_para_o_peso(apps, schema_editor):
    NotaAvaliacao = apps.get_model('aluno', 'NotaAvaliacao')
    registros = _registros_de_soma(NotaAvaliacao)
    for registro in registros:
        registro.nota_obtida = round(registro.nota_obtida * registro.avaliacao.valor / 10, 2)
    NotaAvaliacao.objects.bulk_update(registros, ['nota_obtida'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0028_aula_falta_disciplina_total_periodos'),
    ]

    operations = [
        migrations.RunPython(converter_para_0_a_10, voltar_para_o_peso),
    ]
