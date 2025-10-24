import os
import shutil

# Obtém o caminho absoluto da pasta atual (onde o script está)
base_dir = os.path.dirname(os.path.abspath(__file__))

# Corrigido: sua pasta real é "src"
pasta_origem = base_dir  # zipará tudo que está no mesmo local do script
# ou, se o script está fora da src, use:
# pasta_origem = os.path.join(base_dir, "src")

# Caminho do arquivo ZIP final
arquivo_zip = os.path.join(base_dir, "projeto.zip")

print(f"Compactando: {pasta_origem}")
print(f"Destino: {arquivo_zip}")

if not os.path.exists(pasta_origem):
    raise FileNotFoundError(f"A pasta '{pasta_origem}' não existe!")

# Cria o arquivo ZIP
shutil.make_archive(
    base_name=arquivo_zip.replace(".zip", ""),
    format="zip",
    root_dir=pasta_origem
)

print(f"✅ Arquivo ZIP criado com sucesso em: {arquivo_zip}")
