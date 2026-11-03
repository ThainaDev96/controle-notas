// Tabela de notas, a mesma na Lista de Notas (data-modo="editar") e no Cadastro de Notas (data-modo="lancar").
// Na Lista só as notas lançadas vêm abertas para corrigir, a que for limpa não abre mais e o exame é só para ver.
// No Cadastro só a célula em branco abre, com duplo clique, e depois de lançada não abre mais.
// Nas duas, Tab e Shift+Tab salvam a célula como ao sair dela e abrem a próxima (ou a anterior) que pode receber nota.
$(document).ready(function () {
    var $tabela = $('#TabelaNotas');
    if (!$tabela.length) {
        return;
    }

    var modoLancar = $tabela.attr('data-modo') === 'lancar';
    var erroPadrao = 'Não foi possível salvar. Recarregue a página e tente de novo.';
    // As notas são enviadas uma de cada vez, para as respostas chegarem na ordem em que foram salvas.
    var fila = $.when();

    var tabela = $tabela.DataTable({
        language: {
            "url": "https://cdn.datatables.net/plug-ins/1.13.4/i18n/pt-BR.json"
        },
        // A tabela só se monta quando esses textos chegam, e se montar com uma célula em edição tira o foco
        // dela, o que gravaria a nota pela metade. Por isso as células só abrem depois de montada.
        initComplete: function () {
            if (modoLancar) {
                $tabela.on('dblclick', 'td.editavel', function () {
                    // Duplo clique dentro de uma célula já aberta só seleciona o que foi digitado.
                    if (!this.isContentEditable) {
                        abrirCelula($(this));
                        this.focus();
                    }
                });
            } else {
                this.api().$('td.editavel').each(function () {
                    abrirCelula($(this));
                });
            }
        }
    });

    function abrirCelula($celula) {
        $celula.addClass('editavel').attr('contenteditable', 'true').data('original', $celula.text().trim());
    }

    // Células que podem receber nota, na ordem em que a tabela mostra as linhas (com a ordenação e a busca
    // escolhidas), de todas as páginas. A que está salvando não entra: o blur tira o "editavel" dela.
    function celulasEmOrdem() {
        var celulas = [];
        tabela.rows({ order: 'applied', search: 'applied' }).nodes().each(function (linha) {
            $(linha).children('td.editavel').each(function () {
                celulas.push(this);
            });
        });
        return celulas;
    }

    // Abre a célula e põe o cursor nela, trocando de página da tabela se ela estiver em outra.
    function irParaCelula(celula) {
        var pagina = tabela.page.info();
        if (pagina.length > 0) {
            var posicao = tabela.rows({ order: 'applied', search: 'applied' }).nodes().indexOf(celula.parentNode);
            var paginaDaCelula = Math.floor(posicao / pagina.length);
            if (paginaDaCelula !== pagina.page) {
                // 'page' só troca a página, sem reordenar nem filtrar as linhas de novo.
                tabela.page(paginaDaCelula).draw('page');
            }
        }
        if (!celula.isContentEditable) {
            abrirCelula($(celula));
        }
        celula.focus();
        // Seleciona o que já está na célula, como o Tab faz num campo de texto.
        window.getSelection().selectAllChildren(celula);
    }

    function atualizarAviso() {
        // tabela.$ procura em todas as linhas, inclusive nas de outras páginas da tabela.
        $('#aviso-todas').toggleClass('d-none', tabela.$('td.editavel').length > 0);
    }

    // A nota não entrou: a célula volta a mostrar o que tinha, sem parecer uma nota salva.
    function naoSalvou($celula, original, mensagem) {
        $celula.text(original).addClass('editavel');
        if (!modoLancar) {
            abrirCelula($celula);
        }
        tabela.row($celula.closest('tr')).invalidate('dom');
        atualizarAviso();
        // A linha pode já ter saído da tela (outra página da tabela): a mensagem diz de qual nota se trata.
        alert($celula.attr('data-rotulo') + ': ' + mensagem);
    }

    // No Cadastro, quem caiu em exame tem a célula do exame aberta em branco, sem precisar recarregar a página.
    function mostrarExame($linha, resp) {
        var $exame = $linha.find('.col-exame');

        if (!modoLancar || !resp.exame_aberto) {
            $exame.removeClass('editavel').removeAttr('contenteditable data-nota data-rotulo').text(resp.nota_exame);
        } else if (!$exame.attr('data-nota')) {
            $exame.empty().addClass('editavel').attr({
                'data-nota': resp.nota_id,
                'data-rotulo': $linha.children().eq(1).text() + ', Exame'
            });
        }
    }

    function salvarNota($celula, valor, original) {
        var $linha = $celula.closest('tr');

        return $.post($tabela.attr('data-url-nota'), {
            aluno: $linha.attr('data-aluno'),
            turma: $tabela.attr('data-turma'),
            avaliacao: $celula.attr('data-avaliacao'),
            valor: valor,
            so_lancar: modoLancar ? '1' : '',
            csrfmiddlewaretoken: $tabela.attr('data-csrf')
        }).done(function (resp) {
            if (resp && (resp.ok || resp.ja_lancada)) {
                $celula.text(resp.valor);
                if (!modoLancar && resp.valor !== '') {
                    abrirCelula($celula);
                }
                $linha.find('.col-mf').text(resp.media_final);
                $linha.find('.col-situacao').text(resp.situacao);
                mostrarExame($linha, resp);
                // Atualiza o que o DataTables guardou da linha, para a busca e a ordenação usarem os valores novos.
                tabela.row($linha).invalidate('dom');
                atualizarAviso();
                if (resp.ja_lancada) {
                    alert($celula.attr('data-rotulo') + ': ' + resp.erro);
                }
            } else {
                naoSalvou($celula, original, (resp && resp.erro) || erroPadrao);
            }
        }).fail(function () {
            naoSalvou($celula, original, erroPadrao);
        });
    }

    function salvarExame($celula, valor, original) {
        var $linha = $celula.closest('tr');

        return $.post($tabela.attr('data-url-exame'), {
            id: $celula.attr('data-nota'),
            valor: valor,
            csrfmiddlewaretoken: $tabela.attr('data-csrf')
        }).done(function (resp) {
            if (resp && resp.ok) {
                // Sem data-nota a célula fica fechada, só mostrando o exame lançado.
                $celula.text(resp.nota_exame).removeAttr('data-nota data-rotulo');
                $linha.find('.col-situacao').text(resp.situacao);
                tabela.row($linha).invalidate('dom');
                atualizarAviso();
            } else {
                naoSalvou($celula, original, (resp && resp.erro) || erroPadrao);
            }
        }).fail(function () {
            naoSalvou($celula, original, erroPadrao);
        });
    }

    $tabela.on('blur', 'td[contenteditable="true"]', function () {
        var $celula = $(this);

        // Trocar de janela também dispara o blur, mas a célula continua ativa: a pessoa ainda não saiu dela.
        if (document.activeElement === this) {
            return;
        }

        var valor = $celula.text().trim();
        var original = $celula.data('original');

        // Sair sem mudar nada não salva nem recalcula.
        if (valor === original) {
            if (modoLancar) {
                $celula.removeAttr('contenteditable').empty();
            }
            return;
        }

        // Enquanto salva, a célula não abre de novo.
        $celula.removeAttr('contenteditable').removeClass('editavel');

        function enviar() {
            return $celula.is('[data-nota]') ? salvarExame($celula, valor, original) : salvarNota($celula, valor, original);
        }
        fila = fila.then(enviar, enviar);
    });

    $tabela.on('keydown', 'td[contenteditable="true"]', function (evento) {
        if (evento.key === 'Enter') {
            evento.preventDefault();
            $(this).blur();
        } else if (evento.key === 'Tab') {
            // A lista sai antes do blur, enquanto esta célula ainda conta como aberta.
            var celulas = celulasEmOrdem();
            var destino = celulas[celulas.indexOf(this) + (evento.shiftKey ? -1 : 1)];
            // Na primeira e na última célula o Tab sai da tabela, como de costume, e o blur salva.
            if (!destino) {
                return;
            }
            evento.preventDefault();
            // Salva primeiro, como ao sair da célula; só depois troca de página, se precisar.
            $(this).blur();
            irParaCelula(destino);
        }
    });
});
