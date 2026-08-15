$(document).on('click', '.btn-deletar', function (e) {
    e.preventDefault();
    const url = $(this).data('url');
    const nome = $(this).data('nome');

    $('#nome-item-modal').text(nome);
    $('#btn-confirmar-deletar').attr('href', url);
    $('#modalDeletar').modal('show');
});