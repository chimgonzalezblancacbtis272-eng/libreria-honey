from flask import Flask, render_template, request, redirect, url_for, abort, send_file
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from gridfs import GridFSBucket
import os


app = Flask(__name__)

# Tamaño máximo de un PDF: 25 MB
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


# ==========================================
# CONEXIÓN CON MONGODB ATLAS
# ==========================================

mongo_uri = os.environ.get("MONGO_URI")

if not mongo_uri:
    if os.environ.get("RENDER"):
        raise RuntimeError("Configura MONGO_URI en Render.")

    mongo_uri = "mongodb://127.0.0.1:27017/"


cliente = MongoClient(
    mongo_uri,
    serverSelectionTimeoutMS=10000
)


# Nombre de nuestra base de datos
base_datos = cliente["LIBRERIA_HONEY"]


# Colección donde guardaremos los datos de los libros
libros = base_datos["libros"]


# GridFS será utilizado para guardar los PDF
pdfs = GridFSBucket(
    base_datos,
    bucket_name="pdfs"
)


# ==========================================
# CATEGORÍAS
# ==========================================

CATEGORIAS = [
    "Libros de la escuela",
    "Ficción",
    "Terror",
    "Romance"
]


# ==========================================
# BUSCAR LIBRO
# ==========================================

def buscar_libro(id):

    if not ObjectId.is_valid(id):
        abort(
            404,
            description="El libro no tiene una clave válida."
        )

    libro = libros.find_one(
        {
            "_id": ObjectId(id)
        }
    )

    if libro is None:
        abort(
            404,
            description="Este libro no existe."
        )

    return libro


# ==========================================
# GUARDAR PDF
# ==========================================

def guardar_pdf(archivo, titulo):

    if archivo is None or archivo.filename == "":
        abort(
            400,
            description="Selecciona un archivo PDF."
        )

    nombre = archivo.filename.strip()

    # Revisamos que realmente sea PDF
    if not nombre.lower().endswith(".pdf"):
        abort(
            400,
            description="Solo puedes subir archivos PDF."
        )

    archivo_id = pdfs.upload_from_stream(
        nombre,
        archivo.stream,
        metadata={
            "titulo": titulo,
            "tipo": "libro",
            "contentType": "application/pdf"
        }
    )

    return archivo_id


# ==========================================
# LEER LOS DATOS DEL FORMULARIO
# ==========================================

def guardar_portada(archivo, titulo):
    """Guarda una imagen opcional de portada en GridFS."""
    if not archivo or not archivo.filename:
        return None
    nombre = archivo.filename.lower()
    extensiones = {".jpg", ".jpeg", ".png", ".webp"}
    extension = os.path.splitext(nombre)[1]
    if extension not in extensiones:
        abort(400, description="La portada debe ser JPG, PNG o WEBP.")
    return pdfs.upload_from_stream(
        f"portada-{os.path.basename(archivo.filename)}",
        archivo.stream,
        metadata={"titulo": titulo, "tipo": "portada", "contentType": {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp"
        }[extension]}
    )


def leer_formulario():

    titulo = request.form.get(
        "titulo",
        ""
    ).strip()

    categoria = request.form.get(
        "categoria",
        ""
    ).strip()


    if not titulo or not categoria:

        abort(
            400,
            description="Completa el nombre del libro y la categoría."
        )


    if categoria not in CATEGORIAS:

        abort(
            400,
            description="Selecciona una categoría válida."
        )


    return {
        "titulo": titulo,
        "categoria": categoria
    }


# ==========================================
# PÁGINA PRINCIPAL
# ==========================================

@app.route("/")
def inicio():

    categoria = request.args.get(
        "categoria",
        ""
    ).strip()


    if categoria in CATEGORIAS:

        lista = list(
            libros.find(
                {
                    "categoria": categoria
                }
            ).sort(
                "titulo",
                1
            )
        )

    else:

        categoria = ""

        lista = list(
            libros.find().sort(
                "titulo",
                1
            )
        )


    # Contar libros de cada categoría
    cantidades = {}

    for nombre in CATEGORIAS:

        cantidades[nombre] = libros.count_documents(
            {
                "categoria": nombre
            }
        )


    return render_template(
        "index.html",
        libros=lista,
        categorias=CATEGORIAS,
        cantidades=cantidades,
        categoria_actual=categoria
    )


# ==========================================
# AGREGAR LIBRO
# ==========================================

@app.route(
    "/agregar",
    methods=["POST"]
)
def agregar():

    datos = leer_formulario()

    archivo = request.files.get("pdf")


    if archivo is None or archivo.filename == "":

        abort(
            400,
            description="Debes seleccionar el PDF del libro."
        )


    # Guardamos primero el PDF
    pdf_id = guardar_pdf(
        archivo,
        datos["titulo"]
    )


    # Guardamos el ID del PDF junto con el libro
    datos["pdf_id"] = pdf_id

    datos["nombre_pdf"] = archivo.filename
    portada = guardar_portada(request.files.get("portada"), datos["titulo"])
    if portada:
        datos["portada_id"] = portada


    try:

        libros.insert_one(datos)

    except Exception:

        # Si hubo error, eliminamos el PDF
        pdfs.delete(pdf_id)

        raise


    return redirect(
        url_for("inicio")
    )


# ==========================================
# VER PDF
# ==========================================

@app.route("/portada/<id>")
def ver_portada(id):
    libro = buscar_libro(id)
    portada_id = libro.get("portada_id")
    if not portada_id:
        abort(404, description="Este libro no tiene portada personalizada.")
    try:
        archivo = pdfs.open_download_stream(portada_id)
    except Exception:
        abort(404, description="No se encontró la portada.")
    tipo = (archivo.metadata or {}).get("contentType", "image/jpeg")
    return send_file(archivo, mimetype=tipo, as_attachment=False)


@app.route("/pdf/<id>")
def ver_pdf(id):

    libro = buscar_libro(id)


    if not libro.get("pdf_id"):

        abort(
            404,
            description="Este libro no tiene un PDF guardado."
        )


    try:

        pdf = pdfs.open_download_stream(
            libro["pdf_id"]
        )

    except Exception:

        abort(
            404,
            description="No se encontró el PDF de este libro."
        )


    return send_file(

        pdf,

        mimetype="application/pdf",

        download_name=libro.get(
            "nombre_pdf",
            f"{libro['titulo']}.pdf"
        ),

        as_attachment=False
    )


# ==========================================
# EDITAR LIBRO
# ==========================================

@app.route(
    "/editar/<id>",
    methods=["GET", "POST"]
)
def editar(id):

    libro = buscar_libro(id)


    if request.method == "POST":

        datos = leer_formulario()

        archivo = request.files.get("pdf")


        nuevo_pdf_id = None

        pdf_anterior = libro.get(
            "pdf_id"
        )


        # Si el usuario seleccionó otro PDF
        if archivo and archivo.filename:

            nuevo_pdf_id = guardar_pdf(
                archivo,
                datos["titulo"]
            )

            datos["pdf_id"] = nuevo_pdf_id

            datos["nombre_pdf"] = archivo.filename

        portada_archivo = request.files.get("portada")
        nueva_portada_id = guardar_portada(portada_archivo, datos["titulo"])
        portada_anterior = libro.get("portada_id")
        if nueva_portada_id:
            datos["portada_id"] = nueva_portada_id


        libros.update_one(

            {
                "_id": libro["_id"]
            },

            {
                "$set": datos
            }
        )


        if nueva_portada_id and portada_anterior:
            try:
                pdfs.delete(portada_anterior)
            except Exception:
                pass

        # Eliminar PDF anterior si fue reemplazado
        if nuevo_pdf_id and pdf_anterior:

            try:

                pdfs.delete(
                    pdf_anterior
                )

            except Exception:

                pass


        return redirect(
            url_for("inicio")
        )


    return render_template(

        "editar.html",

        libro=libro,

        categorias=CATEGORIAS
    )


# ==========================================
# ELIMINAR LIBRO
# ==========================================

@app.route(
    "/eliminar/<id>",
    methods=["POST"]
)
def eliminar(id):

    libro = buscar_libro(id)


    # Eliminar el PDF
    if libro.get("pdf_id"):

        try:

            pdfs.delete(
                libro["pdf_id"]
            )

        except Exception:

            pass


    if libro.get("portada_id"):
        try:
            pdfs.delete(libro["portada_id"])
        except Exception:
            pass

    # Eliminar los datos del libro
    libros.delete_one(
        {
            "_id": libro["_id"]
        }
    )


    return redirect(
        url_for("inicio")
    )


# ==========================================
# ERRORES DE MONGODB
# ==========================================

@app.errorhandler(PyMongoError)
def error_mongo(error):

    return render_template(

        "error.html",

        mensaje=
        "No fue posible conectar con MongoDB. "
        "Revisa tu MONGO_URI y vuelve a intentar."

    ), 503


# ==========================================
# PDF DEMASIADO GRANDE
# ==========================================

@app.errorhandler(413)
def archivo_grande(error):

    return render_template(

        "error.html",

        mensaje=
        "El PDF es demasiado grande. "
        "El límite es de 25 MB."

    ), 413


# ==========================================
# OTROS ERRORES
# ==========================================

@app.errorhandler(400)
@app.errorhandler(404)
def error_peticion(error):

    return render_template(

        "error.html",

        mensaje=error.description

    ), error.code


# ==========================================
# AYUDA
# ==========================================

@app.route("/ayuda")
def ayuda():

    return render_template(
        "ayuda.html"
    )


# ==========================================
# EJECUTAR
# ==========================================

if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=puerto,
        debug=False
    )