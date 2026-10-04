// Comportamento do dashboard: ano letivo, rosca da situação final e tabela de alunos em risco.
// Tudo o que os gráficos precisam já vem calculado da view em <script id="da-dados">;
// os selects de cada card só trocam qual pedaço desses dados está na tela.
var Dashboard = {
    dados: null,

    numero: function (valor) {
        return valor.toFixed(1).replace('.', ',');
    },

    alunos: function (quantidade) {
        return quantidade + (quantidade === 1 ? ' aluno' : ' alunos');
    }
};

$(document).ready(function () {
    Dashboard.dados = JSON.parse(document.getElementById('da-dados').textContent);

    var dados = Dashboard.dados;
    var SITUACOES = ['aprovado', 'exame', 'reprovado'];
    var CORES = ['#03458F', '#C4D0E9', '#B4244A'];

    // ANO LETIVO: recarrega a página com ?ano=, porque muda todos os cards de uma vez
    $('#da-ano').on('change', function () {
        this.form.submit();
    });

    // SITUAÇÃO FINAL (rosca)
    var pizzaTurma = document.getElementById('da-pizza-turma');
    var pizzaDisciplina = document.getElementById('da-pizza-disciplina');
    var roscaCanvas = document.getElementById('da-rosca-canvas');
    var disciplinaEscolhida = null;

    var rosca = new Chart(roscaCanvas, {
        type: 'doughnut',
        data: {
            labels: ['Aprovados', 'Em exame', 'Reprovados'],
            datasets: [{
                data: [0, 0, 0],
                backgroundColor: CORES,
                hoverBackgroundColor: CORES,
                borderWidth: 0
            }]
        },
        options: {
            responsive: false,
            cutoutPercentage: 69,
            rotation: -0.5 * Math.PI,
            legend: { display: false },
            elements: { arc: { borderWidth: 0 } },
            tooltips: {
                backgroundColor: '#022B59',
                bodyFontFamily: '"Open Sans", sans-serif',
                displayColors: false
            }
        }
    });

    function preencherDisciplinas() {
        var turma = dados.turmas[pizzaTurma.value];
        $(pizzaDisciplina).empty();
        if (!turma) {
            return;
        }
        // Ao trocar de turma, mantém a disciplina que já estava escolhida (se a turma tiver)
        turma.disciplinas.forEach(function (disciplina, indice) {
            $('<option>')
                .val(indice)
                .text(disciplina.nome)
                .prop('selected', disciplina.id === disciplinaEscolhida)
                .appendTo(pizzaDisciplina);
        });
    }

    function atualizarPizza() {
        var turma = dados.turmas[pizzaTurma.value];
        var disciplina = turma && turma.disciplinas[pizzaDisciplina.value];
        var valores = SITUACOES.map(function (situacao) {
            return disciplina ? disciplina[situacao] : 0;
        });
        var total = valores[0] + valores[1] + valores[2];
        var escopo = disciplina ? 'Turma ' + turma.nome + ', ' + disciplina.nome : 'nenhuma turma cadastrada';

        if (disciplina) {
            disciplinaEscolhida = disciplina.id;
        }

        $('#da-pizza-escopo').text('Mostrando: ' + escopo);
        $('#da-rosca-total').text(total);
        $('#da-rosca-rotulo').text(total === 1 ? 'aluno' : 'alunos');

        SITUACOES.forEach(function (situacao, indice) {
            var percentual = total ? valores[indice] / total * 100 : 0;
            $('#da-legenda-qtd-' + situacao).text(valores[indice]);
            $('#da-legenda-pct-' + situacao).text(Dashboard.numero(percentual) + '%');
        });

        roscaCanvas.setAttribute('aria-label',
            'Situação final, ' + escopo + '. Aprovados: ' + valores[0] +
            ', em exame: ' + valores[1] + ', reprovados: ' + valores[2]);

        rosca.data.datasets[0].data = valores;
        rosca.update();
    }

    $(pizzaTurma).on('change', function () {
        preencherDisciplinas();
        atualizarPizza();
    });
    $(pizzaDisciplina).on('change', atualizarPizza);

    preencherDisciplinas();
    atualizarPizza();

    // ALUNOS EM RISCO
    $('#TabelaAlunosRisco').DataTable({
        order: [],   // mantém a ordem da view: nome do aluno, depois disciplina
        language: {
            "url": "https://cdn.datatables.net/plug-ins/1.13.4/i18n/pt-BR.json"
        }
    });
});
