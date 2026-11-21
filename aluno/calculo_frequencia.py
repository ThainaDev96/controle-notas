from decimal import Decimal, ROUND_HALF_UP


def calcular_frequencia(total_periodos, faltas):
    """
    Frequência do aluno na disciplina, em texto: (total_periodos - faltas) / total_periodos x 100,
    com duas casas e vírgula (ex.: "94,44%"). Sem total de períodos informado, devolve "-".
    """
    if not total_periodos:
        return "-"

    frequencia = Decimal(total_periodos - faltas) / Decimal(total_periodos) * 100
    frequencia = frequencia.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{frequencia}".replace('.', ',') + "%"


# Frequência mínima para não reprovar por falta, em %.
FREQUENCIA_MINIMA = 75


def limite_de_faltas(total_periodos):
    """
    Quantidade de faltas a partir da qual a frequência fica abaixo da mínima e o aluno reprova por falta
    (ex.: 19 em 72 períodos; com 18 a frequência é 75% exatos e ainda não reprova).
    Sem total de períodos informado não há frequência, e devolve None.
    """
    if not total_periodos:
        return None
    return total_periodos * (100 - FREQUENCIA_MINIMA) // 100 + 1


def reprovado_por_falta(total_periodos, faltas):
    """Frequência abaixo da mínima. A falta justificada conta como as outras."""
    limite = limite_de_faltas(total_periodos)
    return limite is not None and faltas >= limite
