# As notas de avaliação vão de 0 a 10 em todas as telas.
NOTA_MAXIMA = 10


def calcular_nota_final(disciplina, notas_avaliacao):
    """
    notas_avaliacao: lista de tuplas (avaliacao, nota) do aluno,
    já filtradas pela disciplina e pelo ano corretos.
    """
    modo = disciplina.modo_calculo
    validas = [(avaliacao, nota) for avaliacao, nota in notas_avaliacao if nota is not None]

    if not validas:
        return None

    if modo == 'soma':
        lista_notas = [nota or 0 for avaliacao, nota in validas]
        
        # sum() soma todos os elementos da lista e retorna o valor total
        soma_das_notas = sum(lista_notas)
        
        # round() Arredonda para 2 casas decimais
        nota_final = round(soma_das_notas, 2)

        return nota_final

    if modo == 'ponderada':
        lista_ponderada = [nota * (avaliacao.valor) for avaliacao, nota in validas]
        
        nota_final = sum(lista_ponderada) / 100
        
        return round(nota_final, 2)

    if modo == 'aritmetica':
        lista_notas = [nota for avaliacao, nota in validas]
        soma_das_notas = sum(lista_notas)
        qtde_avaliacoes = len(validas)

        nota_final = soma_das_notas / qtde_avaliacoes

        return round(nota_final, 2)

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


def _fmt_pt(valor):
    # A segunda casa só aparece quando existe: 8,0 continua assim, mas 6,99 não pode virar 7,0.
    texto = f"{valor:.2f}"
    if texto.endswith('0'):
        texto = texto[:-1]
    return texto.replace('.', ',')


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
