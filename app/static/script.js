let arquivosSelecionados = [];

function mostrarMensagem(mensagem, tipo = 'sucesso') {
    const msgDiv = document.getElementById('mensagem-feedback');
    msgDiv.textContent = mensagem;
    msgDiv.className = tipo;
    msgDiv.style.display = 'block';
    setTimeout(() => {
        msgDiv.style.display = 'none';
    }, 5000);
}

function mostrarLoading(mostrar = true) {
    const loadingDiv = document.getElementById('loading-spinner');
    loadingDiv.style.display = mostrar ? 'block' : 'none';
}

function adicionarArquivo() {
    const input = document.getElementById('file-input');
    const novosArquivos = Array.from(input.files);

    arquivosSelecionados.push(...novosArquivos);
    input.value = '';
    atualizarLista();
}

function adicionarArquivoSplit() {
    const input = document.getElementById('file-input');
    const novosArquivos = Array.from(input.files);

    arquivosSelecionados = [];
    arquivosSelecionados.push(...novosArquivos);
    input.value = '';
    atualizarLista();
}

function atualizarLista() {
    const lista = document.getElementById('lista-arquivos');
    if (!lista) return;
    lista.innerHTML = arquivosSelecionados.map(file => `<li>${file.name}</li>`).join('');
}

function enviarArquivosConverter() {
    if (arquivosSelecionados.length === 0) {
        mostrarMensagem("Adicione pelo menos um arquivo para converter.", 'erro');
        return;
    }

    mostrarLoading(true);

    arquivosSelecionados.forEach(file => {
        const formData = new FormData();
        formData.append('file', file);

        fetch('/api/convert', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) throw new Error(`Erro ao converter: ${file.name}`);
            return response.blob();
        })
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${file.name.split('.')[0]}.pdf`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            mostrarMensagem(`Arquivo "${file.name}" convertido com sucesso!`);
        })
        .catch(err => {
            mostrarMensagem(err.message, 'erro');
        })
        .finally(() => {
            mostrarLoading(false);
        });
    });

    arquivosSelecionados = [];
    atualizarLista();
}

function enviarArquivosMerge() {
    if (arquivosSelecionados.length === 0) {
        mostrarMensagem("Adicione pelo menos um PDF.", 'erro');
        return;
    }

    mostrarLoading(true);

    const formData = new FormData();
    arquivosSelecionados.forEach(file => {
        formData.append('files', file);
    });

    fetch('/api/merge', {
        method: 'POST',
        body: formData
    })
    .then(response => {
        if (!response.ok) throw new Error("Erro ao juntar os arquivos PDF.");
        return response.blob();
    })
    .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'pdf_juntado.pdf';
        document.body.appendChild(a);
        a.click();
        a.remove();
        mostrarMensagem("PDFs juntados com sucesso!");
    })
    .catch(err => mostrarMensagem(err.message, 'erro'))
    .finally(() => {
        mostrarLoading(false);
    });

    arquivosSelecionados = [];
    atualizarLista();
}

function enviarArquivosSplit() {
    if (arquivosSelecionados.length !== 1) {
        mostrarMensagem("Selecione exatamente um arquivo PDF para dividir.", 'erro');
        return;
    }

    mostrarLoading(true);

    const formData = new FormData();
    formData.append('file', arquivosSelecionados[0]);

    fetch('/api/split', {
        method: 'POST',
        body: formData
    })
    .then(response => {
        if (!response.ok) throw new Error("Erro ao dividir o PDF.");
        return response.blob();
    })
    .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'pdf_dividido.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        mostrarMensagem("PDF dividido com sucesso!");
    })
    .catch(err => mostrarMensagem(err.message, 'erro'))
    .finally(() => {
        mostrarLoading(false);
    });

    arquivosSelecionados = [];
    atualizarLista();
}