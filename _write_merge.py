# -*- coding: utf-8 -*-
content = """\
{% extends "base.html" %}
{% block title %}Juntar PDFs{% endblock %}
{% block body_attrs %}data-page="merge" data-fab-safe="240"{% endblock %}

{% block header_text %}
  <h2>Juntar PDFs</h2>
{% endblock %}

{% block content %}
<div id="merge-page">
  <main class="merge-main">
    {% set prefix = 'merge' %}

    <div class="merge-layout">
      <!-- WORKSPACE full-width -->
      <section class="workspace" aria-label="Área de trabalho">

        <!-- Upload + ações no topo -->
        <div class="workspace-header">
          <div class="dropzone-container">
            <div
              id="dropzone-{{ prefix }}"
              class="dropzone"
              data-preview="#preview-{{ prefix }}"
              data-spinner="#spinner-{{ prefix }}"
              data-action="#btn-{{ prefix }}"
              data-extensions=".pdf"
              data-multiple="true"
            >
              <input
                type="file"
                id="input-{{ prefix }}"
                name="files"
                accept="application/pdf,.pdf"
                multiple
                class="dz-input-overlay"
                aria-label="Selecionar PDFs para juntar"
              >
              <span>Arraste os arquivos aqui ou clique para selecionar</span>
            </div>
          </div>

          <div class="actions actions--inline" data-no-drag>
            <button id="btn-{{ prefix }}" type="button" class="btn btn-primary" disabled>
              Juntar PDFs
            </button>

            <button id="btn-reset-files" type="button" class="btn btn-secondary" disabled
                    title="Redefinir a ordem para a sequência original de envio">
              Redefinir ordem
            </button>

            <button id="btn-organize-apply" type="button" class="btn btn-secondary" disabled
                    title="Aplicar a ordem definida na lista ao grid de miniaturas">
              Aplicar ordem
            </button>

            <button id="btn-toggle-compact" type="button" class="btn btn-secondary" disabled
                    aria-pressed="true" aria-label="Alternar modo compacto"
                    aria-controls="preview-{{ prefix }}">
              Compacto: Ligado
            </button>

            <button id="btn-clear-all" type="button"
                    class="btn btn-tertiary"
                    title="Remover todos os arquivos e páginas"
                    aria-label="Limpar seleção">
              <svg class="icon" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.8"
                   stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
                <path d="M10 11v6M14 11v6"/>
              </svg>
              Limpar
            </button>
          </div>
        </div>

        <div id="spinner-{{ prefix }}" class="overlay-spinner hidden" aria-hidden="true">
          <div class="loader"></div>
        </div>

        <!-- Feedback de estado e erros -->
        <div id="mensagem-feedback"
             class="hidden"
             role="status"
             aria-live="polite"
             aria-atomic="true"></div>

        <!-- Aviso de ordenação -->
        <div id="compact-hint" class="merge-info-callout" role="note" aria-live="polite" aria-atomic="true">
          <svg class="merge-info-callout__icon" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fill-rule="evenodd" d="M18 10A8 8 0 1 1 2 10a8 8 0 0 1 16 0Zm-7-4a1 1 0 1 1-2 0 1 1 0 0 1 2 0ZM9 9a.75.75 0 0 0 0 1.5h.253l-.414 2.07A.75.75 0 0 0 9.573 14h.854a.75.75 0 0 0 0-1.5h-.12l.414-2.071A.75.75 0 0 0 10.273 9H9Z" clip-rule="evenodd"/>
          </svg>
          <span>
            A ordem final do PDF é definida pela <strong>ordem das miniaturas</strong> — arraste para reorganizar.
            No modo compacto apenas a 1ª página de cada PDF é exibida; use o botão acima para expandir/colapsar
            ou clique no selo da letra (A, B, C…) para expandir um arquivo específico.
          </span>
        </div>

        <!-- GRID DE MINIATURAS -->
        <div
          id="preview-{{ prefix }}"
          class="preview-grid thumb-list thumb-grid"
          data-sortable="true"
          data-prefix="{{ prefix }}"
          data-compact="on"
          aria-live="polite"
          aria-label="Pré-visualização e ordenação dos PDFs e páginas"
          role="listbox"
          aria-multiselectable="true">
        </div>

        <!-- Dicas de uso -->
        <div class="merge-info-callout merge-info-callout--steps" role="note" aria-label="Como juntar PDFs">
          <ul class="mini-steps">
            <li>Envie <strong>2+ PDFs</strong></li>
            <li>Arraste as miniaturas para <strong>definir a ordem das páginas</strong></li>
            <li>(Opcional) selecione páginas específicas para incluir</li>
            <li>Clique <strong>Juntar PDFs</strong></li>
          </ul>
          <details class="more more--inline">
            <summary>Observações</summary>
            <ul>
              <li>PDFs com senha podem falhar.</li>
              <li>Respeita os limites desta instalação.</li>
              <li>Se não baixar, habilite pop-ups.</li>
            </ul>
          </details>
        </div>

      </section>

      <!--
        SIDEBAR: permanentemente oculta mas presente no DOM.
        merge-sidebar.js vincula listeners a #file-list-left e #file-list-right
        via MutationObserver no #preview-merge. Os botões #btn-reset-files e
        #btn-organize-apply foram movidos para o toolbar acima — o JS os
        encontra por ID independente de posição no DOM.
      -->
      <aside id="sidebar" class="tool__sidebar merge-sidebar--ghost"
             aria-hidden="true" hidden>
        <div class="tool__sidebar__body">
          <div class="organize-grid" role="group" aria-label="Listas de arquivos">
            <ol id="file-list-left"
                class="file-list file-list--col"
                role="listbox"
                aria-label="Lista de arquivos"></ol>
            <ol id="file-list-right"
                class="file-list file-list--col"
                role="listbox"
                aria-label="Lista adicional de arquivos"></ol>
          </div>
          <p id="file-list-hint" class="file-list-hint" aria-live="polite"></p>
        </div>
      </aside>

    </div><!-- /.merge-layout -->
  </main>
</div>
{% endblock %}

{% block scripts %}
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='pdfjs/pdf.min.js') }}" defer></script>
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='js/pdf-config.js') }}" defer></script>
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='js/utils.js') }}" defer></script>
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='js/merge-page.js') }}" defer></script>
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='js/merge-sync.js') }}" defer></script>
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='js/merge-sidebar.js') }}" defer></script>
  <script nonce="{{ csp_nonce() }}" src="{{ url_for('static', filename='js/merge-compact.js') }}" defer></script>
{% endblock %}
"""

with open(r'c:\Users\Caio-PC\Desktop\Projeto Ma Alpha\app\templates\merge.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('merge.html reescrito com sucesso.')
