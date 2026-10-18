from flask import Flask, render_template, request, redirect, url_for, abort, send_file, session, flash
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from gridfs import GridFSBucket
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from bson.errors import InvalidId
import os, secrets


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

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
usuarios = base_datos["usuarios"]
mercancia = base_datos["mercancia"]
ofertas = base_datos["ofertas"]
compras = base_datos["compras"]


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

    categoria = request.args.get("categoria", "").strip()
    busqueda = request.args.get("q", "").strip()


    filtro = {}
    if categoria in CATEGORIAS: filtro["categoria"] = categoria
    else: categoria = ""
    if busqueda: filtro["titulo"] = {"$regex": busqueda, "$options": "i"}
    lista = list(libros.find(filtro).sort("titulo", 1))


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
        categoria_actual=categoria,
        busqueda=busqueda,
        ofertas=list(ofertas.find({"activo": True}).sort("posicion", 1).limit(4)),
        cantidad_carrito=len(session.get("carrito", []))
    )


# ==========================================
# AGREGAR LIBRO
# ==========================================

@app.route(
    "/agregar",
    methods=["POST"]
)
def agregar():

    if not usuario_actual() or usuario_actual().get("rol") != "admin":
        flash("Solo administración puede gestionar el catálogo de libros.", "danger")
        return redirect(url_for("inicio"))

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

    if not usuario_actual() or usuario_actual().get("rol") != "admin":
        flash("Solo administración puede gestionar el catálogo de libros.", "danger")
        return redirect(url_for("inicio"))

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

    if not usuario_actual() or usuario_actual().get("rol") != "admin":
        flash("Solo administración puede gestionar el catálogo de libros.", "danger")
        return redirect(url_for("inicio"))

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
# CUENTAS, CARRITO, MERCH Y PANEL ADMINISTRADOR
# ==========================================
def usuario_actual():
    uid = session.get("usuario_id")
    if not uid: return None
    try: return usuarios.find_one({"_id": ObjectId(uid)})
    except Exception: return None

@app.context_processor
def datos_globales():
    return {"usuario_actual": usuario_actual(), "cantidad_carrito": len(session.get("carrito", []))}

def requiere_login(func):
    @wraps(func)
    def decorada(*args, **kwargs):
        if not usuario_actual():
            flash("Inicia sesión para continuar.", "warning")
            return redirect(url_for("iniciar_sesion", siguiente=request.path))
        return func(*args, **kwargs)
    return decorada

def requiere_admin(func):
    @wraps(func)
    def decorada(*args, **kwargs):
        u = usuario_actual()
        if not u or u.get("rol") != "admin":
            flash("Este apartado es exclusivo del administrador.", "danger")
            return redirect(url_for("inicio"))
        return func(*args, **kwargs)
    return decorada

def guardar_imagen(archivo, titulo, tipo="mercancia"):
    if not archivo or not archivo.filename: return None
    ext = os.path.splitext(archivo.filename.lower())[1]
    tipos = {".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png", ".webp":"image/webp"}
    if ext not in tipos: abort(400, description="La imagen debe ser JPG, PNG o WEBP.")
    return pdfs.upload_from_stream("%s-%s" % (tipo, os.path.basename(archivo.filename)), archivo.stream,
        metadata={"titulo": titulo, "tipo": tipo, "contentType": tipos[ext]})

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        clave = request.form.get("clave", "")
        if not nombre or not correo or len(clave) < 8:
            flash("Completa los datos y usa una contraseña de al menos 8 caracteres.", "danger")
        elif usuarios.find_one({"correo": correo}):
            flash("Ya existe una cuenta con ese correo.", "danger")
        else:
            admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
            rol = "admin" if admin_email and correo == admin_email else "cliente"
            usuarios.insert_one({"nombre": nombre, "correo": correo, "clave": generate_password_hash(clave), "rol": rol})
            u = usuarios.find_one({"correo": correo}); session["usuario_id"] = str(u["_id"])
            session.modified = True
            flash("Cuenta creada. ¡Bienvenida/o a Librería Honey!", "success")
            return redirect(url_for("inicio"))
    return render_template("cuenta.html", modo="registro")

@app.route("/iniciar-sesion", methods=["GET", "POST"])
def iniciar_sesion():
    if request.method == "POST":
        correo = request.form.get("correo", "").strip().lower(); clave = request.form.get("clave", "")
        u = usuarios.find_one({"correo": correo})
        admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if u and check_password_hash(u.get("clave", ""), clave):
            session.clear(); session["usuario_id"] = str(u["_id"])
            if admin_email and correo == admin_email: usuarios.update_one({"_id":u["_id"]},{"$set":{"rol":"admin"}})
            flash("Has iniciado sesión.", "success")
            return redirect(request.args.get("siguiente") or url_for("inicio"))
        if admin_email and correo == admin_email and admin_password and secrets.compare_digest(clave, admin_password):
            # Cuenta administrativa se crea con una contraseña segura la primera vez.
            if not u:
                usuarios.insert_one({"nombre":"Administración", "correo":correo, "clave":generate_password_hash(clave), "rol":"admin"})
                u=usuarios.find_one({"correo":correo})
            else: usuarios.update_one({"_id":u["_id"]},{"$set":{"rol":"admin","clave":generate_password_hash(clave)}})
            session.clear(); session["usuario_id"] = str(u["_id"])
            return redirect(url_for("admin"))
        flash("Correo o contraseña incorrectos.", "danger")
    return render_template("cuenta.html", modo="login")

@app.route("/cerrar-sesion")
def cerrar_sesion():
    session.clear(); flash("Sesión cerrada.", "info"); return redirect(url_for("inicio"))

@app.route("/merch")
def ver_merch():
    q = request.args.get("q", "").strip()
    filtro = {"nombre":{"$regex":q,"$options":"i"}} if q else {}
    items = list(mercancia.find(filtro).sort("nombre",1))
    return render_template("merch.html", items=items, busqueda=q)

@app.route("/imagen-merch/<id>")
def imagen_merch(id):
    try: item=mercancia.find_one({"_id":ObjectId(id)})
    except Exception: item=None
    if not item or not item.get("imagen_id"): abort(404)
    f=pdfs.open_download_stream(item["imagen_id"]); return send_file(f,mimetype=(f.metadata or {}).get("contentType","image/jpeg"))

@app.route("/imagen-oferta/<id>")
def imagen_oferta(id):
    try: item=ofertas.find_one({"_id":ObjectId(id)})
    except Exception: item=None
    if not item or not item.get("imagen_id"): abort(404)
    f=pdfs.open_download_stream(item["imagen_id"]); return send_file(f,mimetype=(f.metadata or {}).get("contentType","image/jpeg"))

@app.route("/carrito")
def ver_carrito():
    carrito = session.get("carrito", []); items=[]; total=0
    for linea in carrito:
        try: item=mercancia.find_one({"_id":ObjectId(linea["id"])})
        except Exception: item=None
        if item:
            cantidad=max(1,int(linea.get("cantidad",1))); subtotal=float(item.get("precio",0))*cantidad
            items.append({"producto":item,"cantidad":cantidad,"subtotal":subtotal}); total+=subtotal
    return render_template("carrito.html", items=items, total=total)

@app.route("/carrito/agregar/<id>", methods=["POST"])
def agregar_carrito(id):
    try: item=mercancia.find_one({"_id":ObjectId(id)})
    except Exception: item=None
    if not item: abort(404)
    carrito=session.get("carrito",[]); existente=next((x for x in carrito if x["id"]==id),None)
    if int(item.get("stock",0)) < 1: flash("Este producto está agotado.","warning")
    elif existente:
        if existente["cantidad"] < int(item.get("stock",0)): existente["cantidad"]+=1
        else: flash("No hay más unidades disponibles.","warning")
    else: carrito.append({"id":id,"cantidad":1})
    session["carrito"]=carrito; session.modified=True
    return redirect(request.referrer or url_for("ver_merch"))

@app.route("/carrito/eliminar/<id>", methods=["POST"])
def quitar_carrito(id):
    session["carrito"]=[x for x in session.get("carrito",[]) if x["id"] != id]; session.modified=True
    flash("Producto eliminado de tu carrito.","info"); return redirect(url_for("ver_carrito"))

@app.route("/carrito/checkout", methods=["GET","POST"])
@requiere_login
def checkout():
    carrito=session.get("carrito",[])
    if not carrito: return redirect(url_for("ver_carrito"))
    if request.method=="POST":
        direccion=request.form.get("direccion","").strip(); titular=request.form.get("titular","").strip()
        numero=''.join(ch for ch in request.form.get("tarjeta","") if ch.isdigit())
        vencimiento=request.form.get("vencimiento","").strip(); cvv=request.form.get("cvv","").strip()
        if not direccion or not titular or len(numero) not in (15,16) or len(cvv) not in (3,4) or not vencimiento:
            flash("Revisa tu dirección y los datos de pago de prueba.","danger")
        else:
            lineas=[]; total=0; error_stock=False
            for linea in carrito:
                item=mercancia.find_one({"_id":ObjectId(linea["id"])})
                if not item or int(item.get("stock",0)) < int(linea["cantidad"]): error_stock=True; break
                subtotal=float(item["precio"])*int(linea["cantidad"]); total+=subtotal
                lineas.append({"producto_id":item["_id"],"nombre":item["nombre"],"precio":float(item["precio"]),"cantidad":int(linea["cantidad"]),"subtotal":subtotal})
            if error_stock: flash("Cambió el stock de un producto. Actualiza tu carrito.","warning")
            else:
                for l in lineas: mercancia.update_one({"_id":l["producto_id"]},{"$inc":{"stock":-l["cantidad"]}})
                compras.insert_one({"usuario_id":ObjectId(session["usuario_id"]),"direccion":direccion,"productos":lineas,"total":total,"estado":"registrado (demo)","fecha":__import__('datetime').datetime.utcnow()})
                session["carrito"]=[]; session.modified=True
                flash("Pedido registrado en modo demostración. No se realizó ningún cobro.","success")
                return redirect(url_for("inicio"))
    return render_template("checkout.html")

@app.route("/admin")
@requiere_admin
def admin():
    return render_template("admin.html", items=list(mercancia.find().sort("nombre",1)), todos_ofertas=list(ofertas.find().sort("posicion",1)), libros_admin=list(libros.find().sort("titulo",1)), categorias=CATEGORIAS)

@app.route("/admin/merch/guardar", methods=["POST"])
@requiere_admin
def admin_guardar_merch():
    id=request.form.get("id","").strip(); nombre=request.form.get("nombre","").strip(); descripcion=request.form.get("descripcion","").strip()
    try: precio=float(request.form.get("precio","0")); stock=int(request.form.get("stock","0"))
    except ValueError: abort(400,description="Precio y stock deben ser números válidos.")
    if not nombre or precio<0 or stock<0: abort(400,description="Revisa nombre, precio y stock.")
    tipo=request.form.get("tipo","Mercancía").strip()
    if tipo not in ("Libro físico", "Mercancía"): tipo="Mercancía"
    datos={"nombre":nombre,"descripcion":descripcion,"precio":precio,"stock":stock,"tipo":tipo}
    imagen=guardar_imagen(request.files.get("imagen"),nombre)
    if imagen: datos["imagen_id"]=imagen
    if id:
        old=mercancia.find_one({"_id":ObjectId(id)})
        mercancia.update_one({"_id":ObjectId(id)},{"$set":datos})
        if imagen and old and old.get("imagen_id"):
            try: pdfs.delete(old["imagen_id"])
            except Exception: pass
    else: mercancia.insert_one(datos)
    flash("Mercancía guardada.","success"); return redirect(url_for("admin"))

@app.route("/admin/merch/eliminar/<id>",methods=["POST"])
@requiere_admin
def admin_eliminar_merch(id):
    item=mercancia.find_one({"_id":ObjectId(id)})
    if item and item.get("imagen_id"):
        try: pdfs.delete(item["imagen_id"])
        except Exception: pass
    mercancia.delete_one({"_id":ObjectId(id)}); session["carrito"]=[x for x in session.get("carrito",[]) if x["id"]!=id]
    flash("Producto eliminado.","info"); return redirect(url_for("admin"))

@app.route("/admin/ofertas/guardar",methods=["POST"])
@requiere_admin
def admin_guardar_oferta():
    id=request.form.get("id","").strip(); titulo=request.form.get("titulo","").strip(); descripcion=request.form.get("descripcion","").strip()
    try: posicion=int(request.form.get("posicion","1"))
    except ValueError: abort(400,description="La posición debe ser un número.")
    if not titulo or posicion not in (1,2,3,4): abort(400,description="Usa título y una posición del 1 al 4.")
    datos={"titulo":titulo,"descripcion":descripcion,"posicion":posicion,"activo":True}
    imagen=guardar_imagen(request.files.get("imagen"),titulo,"oferta")
    if imagen: datos["imagen_id"]=imagen
    if id:
        old=ofertas.find_one({"_id":ObjectId(id)}); ofertas.update_one({"_id":ObjectId(id)},{"$set":datos})
        if imagen and old and old.get("imagen_id"):
            try: pdfs.delete(old["imagen_id"])
            except Exception: pass
    else:
        # Máximo cuatro espacios: si la posición ya existe, se reemplaza esa oferta.
        anterior=ofertas.find_one({"posicion":posicion})
        if anterior:
            if imagen:
                if anterior.get("imagen_id"):
                    try: pdfs.delete(anterior["imagen_id"])
                    except Exception: pass
            elif anterior.get("imagen_id"):
                datos["imagen_id"]=anterior["imagen_id"]
            ofertas.update_one({"_id":anterior["_id"]},{"$set":datos})
        else:
            ofertas.insert_one(datos)
    flash("Oferta/recomendación guardada.","success"); return redirect(url_for("admin"))

@app.route("/admin/ofertas/eliminar/<id>",methods=["POST"])
@requiere_admin
def admin_eliminar_oferta(id):
    item=ofertas.find_one({"_id":ObjectId(id)})
    if item and item.get("imagen_id"):
        try: pdfs.delete(item["imagen_id"])
        except Exception: pass
    ofertas.delete_one({"_id":ObjectId(id)}); return redirect(url_for("admin"))


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