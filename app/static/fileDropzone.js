(function(){
  window.createFileDropzone = function(options){
    const {
      dropzone,
      input,
      list,
      extensions = [],
      multiple = true,
      onChange = function(){}
    } = options;
    let files = [];

    function updateList(){
      if(!list) return;
      list.innerHTML = '';
      files.forEach((f, i) => {
        const li = document.createElement('li');
        li.textContent = f.name;

        const del = document.createElement('button');
        del.className = 'icon-btn';
        del.textContent = '🗑';
        del.addEventListener('click', () => removerArquivo(i));

        const view = document.createElement('button');
        view.className = 'icon-btn';
        view.textContent = '👁';
        view.addEventListener('click', () => visualizarArquivo(i));

        li.appendChild(del);
        li.appendChild(view);
        list.appendChild(li);
      });
    }

    window.removerArquivo = function(index){
      files.splice(index, 1);
      updateList();
      onChange(files);
    };

    window.visualizarArquivo = function(index){
      const file = files[index];
      if(file){
        const url = URL.createObjectURL(file);
        window.open(url, '_blank');
      }
    };

    function validExtension(file){
      if(extensions.length === 0) return true;
      const ext = '.' + file.name.split('.').pop().toLowerCase();
      return extensions.includes(ext);
    }

    function addFiles(newFiles){
      const validFiles = Array.from(newFiles).filter(validExtension);
      if(multiple){
        files = files.concat(validFiles);
      } else if(validFiles.length){
        files = [validFiles[0]];
      }
      updateList();
      onChange(files);
    }

    if(input){
      input.addEventListener('change', e => {
        addFiles(e.target.files);
        input.value = '';
      });
    }

    if(dropzone){
      dropzone.addEventListener('click', () => { if(input) input.click(); });
      dropzone.addEventListener('dragover', e => {
        e.preventDefault();
        dropzone.classList.add('dragover');
      });
      dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
      dropzone.addEventListener('drop', e => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        addFiles(e.dataTransfer.files);
      });
    }

    return {
      getFiles: () => files.slice(),
      clear: () => { files = []; updateList(); onChange(files); }
    };
  };
})();
