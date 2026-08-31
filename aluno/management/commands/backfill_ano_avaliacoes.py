from datetime import datetime
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from aluno.models import Disciplina, Nota, Avaliacao, NotaAvaliacao


class Command(BaseCommand):
    help = (
        "Preenche o campo 'ano' das avaliações existentes, usando os registros de "
        "Nota (que já tem ano) como referência. Quando uma avaliação sem ano tem "
        "notas de mais de um ano letivo, ela é dividida em uma cópia por ano, "
        "preservando as notas já lançadas em cada uma."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Sem essa flag, roda em modo dry-run (só mostra o que faria, não grava nada).'
        )

    def handle(self, *args, **options):
        aplicar = options['aplicar']

        if not aplicar:
            self.stdout.write(self.style.WARNING('Modo DRY-RUN — nada será gravado. Use --aplicar para executar de verdade.\n'))

        with transaction.atomic():
            for disciplina in Disciplina.objects.all().order_by('nome'):
                self._processar_disciplina(disciplina, aplicar)

            if not aplicar:
                transaction.set_rollback(True)

    def _processar_disciplina(self, disciplina, aplicar):
        avaliacoes = list(Avaliacao.objects.filter(disciplina=disciplina, ano__isnull=True))
        if not avaliacoes:
            return

        anos = sorted(Nota.objects.filter(disciplina=disciplina).values_list('ano', flat=True).distinct())
        anos = [a for a in anos if a is not None]
        alunos_por_ano = {
            ano: set(Nota.objects.filter(disciplina=disciplina, ano=ano).values_list('aluno_id', flat=True))
            for ano in anos
        }

        avals_por_nome = defaultdict(list)
        for av in avaliacoes:
            avals_por_nome[av.nome].append(av)

        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{disciplina.nome} (id={disciplina.id}) — anos encontrados: {anos}'))

        for nome, avals in avals_por_nome.items():
            if len(avals) > 1:
                self._marcar_grupo_ja_dividido(avals, alunos_por_ano, aplicar)
            else:
                self._marcar_ou_dividir_avaliacao_unica(avals[0], anos, alunos_por_ano, aplicar)

    def _marcar_grupo_ja_dividido(self, avals, alunos_por_ano, aplicar):
        # Já existe uma cópia por ano (caso comum: P1/P2/T1/T2 duplicados) — só
        # descobre qual cópia é de qual ano comparando quais alunos têm nota nela.
        for av in avals:
            alunos_av = set(NotaAvaliacao.objects.filter(avaliacao=av).values_list('aluno_id', flat=True))
            melhor_ano, melhor_overlap = None, -1
            for ano, alunos_ano in alunos_por_ano.items():
                overlap = len(alunos_av & alunos_ano)
                if overlap > melhor_overlap:
                    melhor_overlap = overlap
                    melhor_ano = ano

            self.stdout.write(
                f'  Avaliacao id={av.id} nome={av.nome!r} ({len(alunos_av)} notas) -> ano={melhor_ano} '
                f'({melhor_overlap} alunos batendo)'
            )
            if aplicar:
                av.ano = melhor_ano
                av.save(update_fields=['ano'])

    def _marcar_ou_dividir_avaliacao_unica(self, av, anos, alunos_por_ano, aplicar):
        alunos_av = set(NotaAvaliacao.objects.filter(avaliacao=av).values_list('aluno_id', flat=True))

        # Só considera um aluno "exclusivo" de um ano se ele NÃO tiver Nota em
        # nenhum outro ano dessa disciplina. Aluno com Nota em mais de um ano é
        # ambíguo (ex: continuou cursando) e não deve ser usado como evidência
        # de que uma nota específica pertence a um ano ou outro.
        alunos_exclusivos_por_ano = {}
        for ano in anos:
            outros = set()
            for outro_ano in anos:
                if outro_ano != ano:
                    outros |= alunos_por_ano.get(outro_ano, set())
            alunos_exclusivos_por_ano[ano] = alunos_por_ano.get(ano, set()) - outros

        overlaps_exclusivos = {ano: len(alunos_av & alunos_exclusivos_por_ano[ano]) for ano in anos}
        anos_com_dados_exclusivos = [ano for ano, qtd in overlaps_exclusivos.items() if qtd > 0]

        if len(anos_com_dados_exclusivos) <= 1:
            ano_escolhido = anos_com_dados_exclusivos[0] if anos_com_dados_exclusivos else (anos[-1] if anos else datetime.now().year)
            self.stdout.write(
                f'  Avaliacao id={av.id} nome={av.nome!r} ({len(alunos_av)} notas) -> ano={ano_escolhido} '
                f'(sem necessidade de dividir)'
            )
            if aplicar:
                av.ano = ano_escolhido
                av.save(update_fields=['ano'])
            return

        # Existem alunos exclusivos de mais de um ano nessa mesma avaliação --
        # aí sim precisa dividir. A avaliação original fica com o ano de maior
        # evidência exclusiva (e com todo aluno ambíguo, por padrão); só move
        # pra cópia nova quem é comprovadamente exclusivo do outro ano.
        ano_principal = max(overlaps_exclusivos, key=overlaps_exclusivos.get)
        self.stdout.write(
            f'  Avaliacao id={av.id} nome={av.nome!r} tem alunos exclusivos de {len(anos_com_dados_exclusivos)} anos '
            f'({anos_com_dados_exclusivos}) -> DIVIDINDO. Fica com ano={ano_principal} '
            f'({overlaps_exclusivos[ano_principal]} alunos exclusivos + ambíguos)'
        )
        if aplicar:
            av.ano = ano_principal
            av.save(update_fields=['ano'])

        for ano in anos_com_dados_exclusivos:
            if ano == ano_principal:
                continue
            alunos_desse_ano = alunos_av & alunos_exclusivos_por_ano[ano]
            self.stdout.write(
                f'    -> nova cópia pra ano={ano} recebendo {len(alunos_desse_ano)} notas (só exclusivos)'
            )
            if aplicar:
                nova_av = Avaliacao.objects.create(
                    nome=av.nome, tipo=av.tipo, valor=av.valor, disciplina=av.disciplina, ano=ano,
                )
                NotaAvaliacao.objects.filter(
                    avaliacao=av, aluno_id__in=alunos_desse_ano
                ).update(avaliacao=nova_av)
