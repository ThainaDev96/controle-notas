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
