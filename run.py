import os
from app import create_app

# Cria a aplicação
app = create_app()

if __name__ == "__main__":
    # Executa em produção por padrão (debug desativado)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    app.run(host=host, port=port, debug=False)
