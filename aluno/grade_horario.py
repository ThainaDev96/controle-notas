"""
Grade de horário do aluno (período da manhã, 5 aulas de 50 minutos por dia).

A grade não fica no banco: ela muda a cada ano letivo, então é montada aqui, com as
disciplinas do sistema. A grade base é a mesma para todas as turmas, e cada turma
começa a semana em um dia diferente dela. Como na grade base nenhuma disciplina se
repete na mesma aula (1ª, 2ª...) em dois dias, duas turmas nunca têm a mesma
disciplina no mesmo dia e horário.
"""

DIAS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta']

INTERVALO = 'Intervalo'

# Horário de cada aula, na ordem do dia; o intervalo fica entre a 3ª e a 4ª aula.
HORARIOS = [
    ('07:00', '07:50'),
    ('07:50', '08:40'),
    ('08:40', '09:30'),
    ('09:30', '09:50'),  # intervalo
    ('09:50', '10:40'),
    ('10:40', '11:30'),
]
POSICAO_DO_INTERVALO = 3

# Uma lista por dia, com as 5 aulas na ordem. Na semana: Matemática e Português com 5 aulas
# (uma por dia), as outras disciplinas com 3. Olhando de cima para baixo, cada posição
# (1ª aula, 2ª aula...) tem 5 disciplinas diferentes: é isso que evita o choque entre turmas.
GRADE_BASE = [
    ['Educação física', 'Matemática', 'Geografia', 'Português', 'História'],
    ['Geografia', 'Física', 'Português', 'Literatura', 'Matemática'],
    ['Português', 'História', 'Física', 'Matemática', 'Educação física'],
    ['Física', 'Português', 'Matemática', 'História', 'Literatura'],
    ['Matemática', 'Literatura', 'Educação física', 'Geografia', 'Português'],
]


def dia_inicial(nome_turma):
    """Em qual dia da grade base a semana da turma começa. O mesmo nome dá sempre o mesmo dia."""
    # O número da série conta em dobro: 1A, 1B, 2A, 2B e 3A começam cada uma em um dia.
    return sum(
        int(letra) * 2 if letra.isdigit() else ord(letra.upper()) - ord('A')
        for letra in nome_turma
    ) % len(GRADE_BASE)


def montar_grade(nome_turma, disciplinas_do_aluno=None):
    """
    Linhas da grade da turma, prontas para a tabela: cada uma com o horário e a aula de cada dia.
    Com disciplinas_do_aluno, a aula de disciplina em que o aluno não está matriculado fica vaga ("-").
    """
    giro = dia_inicial(nome_turma)
    dias = GRADE_BASE[giro:] + GRADE_BASE[:giro]

    linhas = []
    aula = 0
    for posicao, (inicio, fim) in enumerate(HORARIOS):
        if posicao == POSICAO_DO_INTERVALO:
            linhas.append({'inicio': inicio, 'fim': fim, 'intervalo': True, 'aulas': [INTERVALO] * len(DIAS)})
            continue
        aulas = [dia[aula] for dia in dias]
        if disciplinas_do_aluno is not None:
            aulas = [nome if nome in disciplinas_do_aluno else '-' for nome in aulas]
        linhas.append({'inicio': inicio, 'fim': fim, 'intervalo': False, 'aulas': aulas})
        aula += 1
    return linhas
