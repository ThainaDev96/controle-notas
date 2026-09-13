def calcular_nota_final(disciplina, notas_avaliacao):
    """
    notas_avaliacao: lista de tuplas (avaliacao, nota) do aluno,
    já filtradas pela disciplina e pelo ano corretos.
    """
    modo = disciplina.modo_calculo
    validas = [(a, n) for a, n in notas_avaliacao if n is not None]

    if not validas:
        return None

    if modo == 'soma':
        return round(sum(n for _, n in validas), 2)

    if modo == 'ponderada':
        total = sum(n * (a.valor or 0) / 100 for a, n in validas)
        return round(total, 2)

    if modo == 'aritmetica':
        return round(sum(n for _, n in validas) / len(validas), 2)

    return None


def classificar(nota_final):
    if nota_final is None:
        return 'cursando'
    if nota_final >= 7:
        return 'aprovado'
    if nota_final >= 4:
        return 'exame'
    return 'reprovado'


def calcular_pos_exame(nota_final, nota_exame):
    a2 = round((nota_final + nota_exame) / 2, 2)
    return a2, ('aprovado' if a2 >= 6 else 'reprovado')


def teto_nota_avaliacao(disciplina, avaliacao):
    """Valor máximo que pode ser lançado numa NotaAvaliacao dessa avaliação."""
    if disciplina.modo_calculo == 'soma':
        return avaliacao.valor if avaliacao.valor is not None else 10
    return 10


def _fmt_pt(valor):
    return f"{valor:.1f}".replace('.', ',')


def montar_resumo_formula(disciplina, notas_avaliacao):
    """
    Monta a fórmula do modo de cálculo da disciplina, passo a passo, pra exibição.
    notas_avaliacao: lista de tuplas (avaliacao, nota), nota pode ser None.
    """
    modo = disciplina.modo_calculo
    validas = [(a, n) for a, n in notas_avaliacao if n is not None]
    resultado = calcular_nota_final(disciplina, notas_avaliacao)

    if not validas:
        return {'modo': modo, 'formula_texto': '', 'resultado': None, 'tem_dados': False}

    if modo == 'soma':
        parcelas = ' + '.join(_fmt_pt(n) for _, n in validas)
        formula_texto = f"{parcelas} = {_fmt_pt(resultado)}"
    elif modo == 'ponderada':
        parcelas = ' + '.join(f"{_fmt_pt(n)} × {_fmt_pt(a.valor or 0)}%" for a, n in validas)
        formula_texto = f"{parcelas} = {_fmt_pt(resultado)}"
    else:  # aritmetica
        parcelas = ' + '.join(_fmt_pt(n) for _, n in validas)
        formula_texto = f"({parcelas}) ÷ {len(validas)} = {_fmt_pt(resultado)}"

    return {'modo': modo, 'formula_texto': formula_texto, 'resultado': resultado, 'tem_dados': True}
