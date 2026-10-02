import json
import os
import re
import time
from datetime import datetime

import serial
import customtkinter as ctk
from tkinter import messagebox


# 1. CONFIGURACIÓN VISUAL
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

COLOR_FONDO = "#F1F5F9"        # gris muy claro, fondo general
COLOR_TARJETA = "#FFFFFF"      # blanco, fondo de cada tarjeta
COLOR_TEXTO = "#0F172A"        # casi negro, texto principal
COLOR_SUAVE = "#64748B"        # gris medio, etiquetas secundarias
COLOR_ACENTO = "#2563EB"       # azul, elementos destacados
COLOR_VERDE = "#16A34A"        # verde, valor de corriente
COLOR_NARANJA = "#EA580C"      # naranja, valor de potencia
COLOR_ROJO = "#DC2626"         # rojo, acciones destructivas

COLOR_ITEM = "#F1F5F9"             # batería no seleccionada
COLOR_ITEM_HOVER = "#E2E8F0"       # al pasar el mouse
COLOR_ITEM_SEL = "#DBEAFE"         # batería seleccionada
COLOR_ITEM_SEL_HOVER = "#BFDBFE"   # seleccionada + mouse encima
COLOR_CAJA = "#F8FAFC"             # fondo de cajas de texto
COLOR_BOTON_GRIS = "#E2E8F0"
COLOR_BOTON_GRIS_HOVER = "#CBD5E1"
COLOR_ROJO_HOVER = "#FEE2E2"

FUENTE_TITULO = ("Segoe UI", 20, "bold")
FUENTE_SUBTITULO = ("Segoe UI", 16, "bold")
FUENTE_ENCABEZADO = ("Segoe UI", 11, "bold")
FUENTE_VALOR = ("Segoe UI", 24, "bold")
FUENTE_NORMAL = ("Segoe UI", 12)
FUENTE_PEQUENA = ("Segoe UI", 10)
FUENTE_MONO = ("Consolas", 10)

ANCHO_VENTANA = 1100
ALTO_VENTANA = 700
ANCHO_PANEL_LATERAL = 260
RADIO_ESQUINA = 12

# 2. GUARDADO DE DATOS
ARCHIVO_DATOS = "baterias.json"

def cargar_datos():
    if not os.path.exists(ARCHIVO_DATOS):
        return []
    with open(ARCHIVO_DATOS, "r", encoding="utf-8") as f:
        contenido = json.load(f)
    return contenido.get("baterias", [])


def guardar_datos():
    with open(ARCHIVO_DATOS, "w", encoding="utf-8") as f:
        json.dump({"baterias": baterias}, f, indent=2, ensure_ascii=False)


def crear_ficha_bateria(nombre, tipo, cola_pavo="", zona_cola_pavo="", levadura="", zona_levadura="", voltaje_corte=0.30):
    return {
        "nombre": nombre,
        "tipo": tipo,                      # "convencional" u "hongos"
        "cola_pavo": cola_pavo,            # porcentaje, solo si es de hongos
        "zona_cola_pavo": zona_cola_pavo,  # "invernadero" o "casa"
        "levadura": levadura,
        "zona_levadura": zona_levadura,
        "voltaje_corte": voltaje_corte,    # umbral para estimar vida útil
        "mediciones": [],
    }


def crear_registro_medicion(voltaje_V, corriente_uA, potencia_uW):
    return {
        "fecha_hora": datetime.now().isoformat(),
        "voltaje_V": voltaje_V,
        "corriente_uA": corriente_uA,
        "potencia_uW": potencia_uW,
    }


# 3. CÁLCULOS
def acumular_en_el_tiempo(valores, tiempos):
    total = 0.0
    for i in range(1, len(valores)):
        horas_transcurridas = tiempos[i] - tiempos[i - 1]
        valor_promedio = (valores[i] + valores[i - 1]) / 2
        total += valor_promedio * horas_transcurridas
    return total


def calcular_tendencia(tiempos, valores):
    n = len(tiempos)
    suma_t = sum(tiempos)
    suma_v = sum(valores)
    suma_tv = sum(t * v for t, v in zip(tiempos, valores))
    suma_t2 = sum(t ** 2 for t in tiempos)

    cambio_por_hora = (n * suma_tv - suma_t * suma_v) / (n * suma_t2 - suma_t ** 2)
    valor_inicial = (suma_v - cambio_por_hora * suma_t) / n
    return cambio_por_hora, valor_inicial


def calcular_resumen(mediciones, voltaje_corte=0.30):
    if len(mediciones) < 2:
        return None

    # Pasa las fechas a horas transcurridas desde la primera medición
    t0 = datetime.fromisoformat(mediciones[0]["fecha_hora"])
    horas = [(datetime.fromisoformat(m["fecha_hora"]) - t0).total_seconds() / 3600.0 for m in mediciones]

    voltajes = [m["voltaje_V"] for m in mediciones]
    corrientes = [m["corriente_uA"] for m in mediciones]
    potencias = [m["potencia_uW"] for m in mediciones]

    # Totales acumulados a lo largo del periodo medido
    energia_uWh = acumular_en_el_tiempo(potencias, horas)
    carga_uAh = acumular_en_el_tiempo(corrientes, horas)

    # A qué ritmo está cayendo el voltaje
    caida_por_hora, voltaje_inicial = calcular_tendencia(horas, voltajes)

    resultado = {
        "num_mediciones": len(mediciones),
        "horas_cubiertas": horas[-1],
        "energia_uWh": energia_uWh,
        "carga_uAh": carga_uAh,
        "caida_mV_por_hora": caida_por_hora * 1000,  
        "vida_util_horas": None,
        "restante_horas": None,
    }

    # Solo se puede proyectar el agotamiento si el voltaje va bajando
    if caida_por_hora < 0:
        horas_hasta_agotarse = (voltaje_corte - voltaje_inicial) / caida_por_hora
        resultado["vida_util_horas"] = horas_hasta_agotarse
        resultado["restante_horas"] = horas_hasta_agotarse - horas[-1]

    return resultado


def calcular_potencia_promedio(mediciones):
    if not mediciones:
        return None
    return sum(m["potencia_uW"] for m in mediciones) / len(mediciones)


# 4. ARDUINO
BAUDIOS = 9600
SEGUNDOS_REINICIO = 2   # el Arduino se reinicia al abrir el puerto
SEGUNDOS_ESPERA = 8     # máximo esperando la medición completa


class ErrorArduino(Exception):
    pass


def leer_medicion_arduino(puerto):
    try:
        conexion = serial.Serial(puerto, BAUDIOS, timeout=5)
    except Exception as e:
        raise ErrorArduino(f"No se pudo abrir el puerto {puerto}: {e}")

    time.sleep(SEGUNDOS_REINICIO)

    voltaje = corriente = potencia = None
    inicio = time.time()

    try:
        while time.time() - inicio < SEGUNDOS_ESPERA:
            linea = conexion.readline().decode("utf-8", errors="ignore").strip()
            if not linea:
                continue

            if (m := re.search(r"Voltaje[^:]*:\s*([-\d.]+)", linea)):
                voltaje = float(m.group(1))
            if (m := re.search(r"Corriente[^:]*:\s*([-\d.]+)", linea)):
                corriente = float(m.group(1))
            if (m := re.search(r"Potencia[^:]*:\s*([-\d.]+)", linea)):
                potencia = float(m.group(1))

            if None not in (voltaje, corriente, potencia):
                break
    finally:
        conexion.close()

    if None in (voltaje, corriente, potencia):
        raise ErrorArduino(
            "No llegó una medición completa. Revisa que el Arduino esté "
            "conectado, que el Monitor Serial esté cerrado, y que el "
            "puerto sea el correcto."
        )

    return voltaje, corriente, potencia


# 5. INTERFAZ
# Estado del programa
baterias = cargar_datos()
seleccionada = None       
botones_lista = []        

ventana = ctk.CTk()
ventana.title("Gestor de Baterías de Hongos")
ventana.geometry(f"{ANCHO_VENTANA}x{ALTO_VENTANA}")
ventana.configure(fg_color=COLOR_FONDO)
ventana.grid_columnconfigure(1, weight=1)
ventana.grid_rowconfigure(0, weight=1)

# --- Panel lateral izquierdo ---
panel_lateral = ctk.CTkFrame(ventana, width=ANCHO_PANEL_LATERAL, corner_radius=0, fg_color=COLOR_TARJETA)
panel_lateral.grid(row=0, column=0, sticky="nsew")
panel_lateral.grid_propagate(False)

ctk.CTkLabel(panel_lateral, text="BATERÍAS", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", padx=20, pady=(24, 12))

lista_frame = ctk.CTkScrollableFrame(panel_lateral, fg_color="transparent", width=220)
lista_frame.pack(fill="both", expand=True, padx=12)

marco_botones = ctk.CTkFrame(panel_lateral, fg_color="transparent")
marco_botones.pack(fill="x", padx=16, pady=16)

# --- Área principal derecha ---
area = ctk.CTkScrollableFrame(ventana, fg_color=COLOR_FONDO)
area.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)

titulo = ctk.CTkLabel(area, text="Selecciona una batería", font=FUENTE_TITULO, text_color=COLOR_TEXTO)
titulo.pack(anchor="w", pady=(0, 4))

subtitulo = ctk.CTkLabel(area, text="Ninguna batería seleccionada", font=FUENTE_NORMAL, text_color=COLOR_SUAVE)
subtitulo.pack(anchor="w", pady=(0, 20))


def crear_tarjeta(titulo_tarjeta):
    tarjeta = ctk.CTkFrame(area, fg_color=COLOR_TARJETA, corner_radius=RADIO_ESQUINA)
    tarjeta.pack(fill="x", pady=(0, 16))
    ctk.CTkLabel(tarjeta, text=titulo_tarjeta.upper(), font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", padx=20, pady=(16, 8))
    cuerpo = ctk.CTkFrame(tarjeta, fg_color="transparent")
    cuerpo.pack(fill="x", padx=20, pady=(0, 18))
    return cuerpo


# Tarjeta: control del Arduino
cuerpo_arduino = crear_tarjeta("Tomar medición")
ctk.CTkLabel(cuerpo_arduino, text="Puerto:", font=FUENTE_NORMAL, text_color=COLOR_SUAVE).pack(side="left", padx=(0, 8))
entrada_puerto = ctk.CTkEntry(cuerpo_arduino, width=90, height=34)
entrada_puerto.insert(0, "COM3")
entrada_puerto.pack(side="left", padx=(0, 12))

# Tarjeta: última medición (tres números grandes)
cuerpo_valores = crear_tarjeta("Última medición")
cuerpo_valores.grid_columnconfigure((0, 1, 2), weight=1)


def crear_metrica(columna, etiqueta, color):
    bloque = ctk.CTkFrame(cuerpo_valores, fg_color="transparent")
    bloque.grid(row=0, column=columna, sticky="w", padx=(0, 24))
    valor = ctk.CTkLabel(bloque, text="—", font=FUENTE_VALOR, text_color=color)
    valor.pack(anchor="w")
    ctk.CTkLabel(bloque, text=etiqueta, font=FUENTE_PEQUENA, text_color=COLOR_SUAVE).pack(anchor="w")
    return valor


valor_voltaje = crear_metrica(0, "Voltaje (V)", COLOR_ACENTO)
valor_corriente = crear_metrica(1, "Corriente (µA)", COLOR_VERDE)
valor_potencia = crear_metrica(2, "Potencia (µW)", COLOR_NARANJA)

fecha_medicion = ctk.CTkLabel(cuerpo_valores, text="Sin mediciones registradas", font=FUENTE_PEQUENA, text_color=COLOR_SUAVE)
fecha_medicion.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))

# Tarjeta: ficha técnica
cuerpo_ficha = crear_tarjeta("Ficha técnica")
texto_ficha = ctk.CTkLabel(cuerpo_ficha, text="—", font=FUENTE_NORMAL, text_color=COLOR_TEXTO, justify="left", anchor="w")
texto_ficha.pack(anchor="w")

# Tarjeta: resumen calculado
cuerpo_resumen = crear_tarjeta("Resumen calculado")
texto_resumen = ctk.CTkLabel(cuerpo_resumen, text="Se necesitan al menos 2 mediciones.", font=FUENTE_NORMAL, text_color=COLOR_TEXTO, justify="left", anchor="w")
texto_resumen.pack(anchor="w")

# Tarjeta: historial
cuerpo_historial = crear_tarjeta("Historial de mediciones")
ctk.CTkLabel(cuerpo_historial, text="Fecha y hora           Voltaje      Corriente        Potencia", font=FUENTE_MONO, text_color=COLOR_SUAVE).pack(anchor="w", pady=(0, 6))
caja_historial = ctk.CTkTextbox(cuerpo_historial, height=180, font=FUENTE_MONO, fg_color=COLOR_CAJA, text_color=COLOR_TEXTO, border_width=0)
caja_historial.pack(fill="x")
caja_historial.configure(state="disabled")


# 6. FUNCIONES DE PANTALLA
def refrescar_lista():
    for b in botones_lista:
        b.destroy()
    botones_lista.clear()

    for i, bat in enumerate(baterias):
        activa = (i == seleccionada)
        etiqueta_tipo = "Hongos" if bat["tipo"] == "hongos" else "Convencional"
        boton = ctk.CTkButton(
            lista_frame,
            text=f"{bat['nombre']}\n{etiqueta_tipo}",
            font=FUENTE_NORMAL,
            fg_color=COLOR_ITEM_SEL if activa else COLOR_ITEM,
            hover_color=COLOR_ITEM_SEL_HOVER if activa else COLOR_ITEM_HOVER,
            text_color=COLOR_ACENTO if activa else COLOR_TEXTO,
            anchor="w", height=50, corner_radius=8,
            command=lambda idx=i: seleccionar_bateria(idx),
        )
        boton.pack(fill="x", pady=3)
        botones_lista.append(boton)


def seleccionar_bateria(indice):
    global seleccionada
    seleccionada = indice
    refrescar_lista()
    actualizar_panel()


def limpiar_panel():
    titulo.configure(text="Selecciona una batería")
    subtitulo.configure(text="Ninguna batería seleccionada")
    valor_voltaje.configure(text="—")
    valor_corriente.configure(text="—")
    valor_potencia.configure(text="—")
    fecha_medicion.configure(text="Sin mediciones registradas")
    texto_ficha.configure(text="—")
    texto_resumen.configure(text="Se necesitan al menos 2 mediciones.")
    caja_historial.configure(state="normal")
    caja_historial.delete("1.0", "end")
    caja_historial.configure(state="disabled")


def actualizar_panel():
    if seleccionada is None:
        return

    bat = baterias[seleccionada]
    mediciones = bat.get("mediciones", [])
    tipo_texto = "Batería de hongos" if bat["tipo"] == "hongos" else "Batería convencional"

    titulo.configure(text=bat["nombre"])
    subtitulo.configure(text=f"{tipo_texto}  ·  {len(mediciones)} mediciones")

    # --- Última medición ---
    if mediciones:
        u = mediciones[-1]
        valor_voltaje.configure(text=f"{u['voltaje_V']:.4f}")
        valor_corriente.configure(text=f"{u['corriente_uA']:,.0f}")
        valor_potencia.configure(text=f"{u['potencia_uW']:,.0f}")
        fecha_medicion.configure(
            text=f"Tomada el {u['fecha_hora'][:10]} a las {u['fecha_hora'][11:19]}")
    else:
        valor_voltaje.configure(text="—")
        valor_corriente.configure(text="—")
        valor_potencia.configure(text="—")
        fecha_medicion.configure(text="Sin mediciones registradas")

    # --- Ficha técnica ---
    ficha = f"Tipo:  {tipo_texto}"
    if bat["tipo"] == "hongos":
        ficha += (f"\nCola de pavo:  {bat['cola_pavo']}%"
                    f"   (cultivo en {bat['zona_cola_pavo']})"
                    f"\nLevadura de cerveza:  {bat['levadura']}%"
                    f"   (cultivo en {bat['zona_levadura']})")
    ficha += f"\nVoltaje de corte:  {bat.get('voltaje_corte', 0.30)} V"
    texto_ficha.configure(text=ficha)

    # --- Resumen calculado ---
    res = calcular_resumen(mediciones, bat.get("voltaje_corte", 0.30))
    if res is None:
        texto_resumen.configure(
            text="Se necesitan al menos 2 mediciones para calcular el resumen.")
    else:
        t = (f"Periodo cubierto:  {res['horas_cubiertas']:.2f} horas\n"
            f"Energía total entregada:  {res['energia_uWh']:.2f} µWh\n"
            f"Carga total entregada:  {res['carga_uAh']:.2f} µAh")
        if res["vida_util_horas"] is not None:
            t += (f"\nTendencia del voltaje:  {res['caida_mV_por_hora']:.2f} mV por hora\n"
                    f"Vida útil estimada:  {res['vida_util_horas']:.2f} h "
                    f"({res['vida_util_horas']/24:.2f} días)\n"
                    f"Tiempo restante estimado:  {res['restante_horas']:.2f} h")
        else:
            t += "\n\nEl voltaje aún no muestra una tendencia a la baja clara."
        texto_resumen.configure(text=t)

    # --- Historial ---
    caja_historial.configure(state="normal")
    caja_historial.delete("1.0", "end")
    if mediciones:
        for m in mediciones:
            caja_historial.insert("end",
                f"{m['fecha_hora'][:10]} {m['fecha_hora'][11:19]}   "
                f"{m['voltaje_V']:>7.4f} V   {m['corriente_uA']:>10.2f} µA   "
                f"{m['potencia_uW']:>10.2f} µW\n")
    else:
        caja_historial.insert("end", "Aún no hay mediciones para esta batería.\n")
    caja_historial.configure(state="disabled")


# 7. ACCIONES
def accion_nueva_bateria():
    dlg = ctk.CTkToplevel(ventana)
    dlg.title("Nueva batería")
    dlg.geometry("460x720")
    dlg.configure(fg_color=COLOR_FONDO)
    dlg.transient(ventana)
    dlg.after(100, dlg.grab_set)

    caja = ctk.CTkFrame(dlg, fg_color=COLOR_TARJETA, corner_radius=RADIO_ESQUINA)
    caja.pack(fill="both", expand=True, padx=20, pady=20)

    ctk.CTkLabel(caja, text="Nueva batería", font=FUENTE_SUBTITULO, text_color=COLOR_TEXTO).pack(anchor="w", padx=24, pady=(20, 16))

    ctk.CTkLabel(caja, text="NOMBRE", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", padx=24)
    entrada_nombre = ctk.CTkEntry(caja, width=360, height=36)
    entrada_nombre.pack(padx=24, pady=(4, 16))
    entrada_nombre.focus()

    ctk.CTkLabel(caja, text="TIPO", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", padx=24)
    tipo_var = ctk.StringVar(value="convencional")
    marco_hongos = ctk.CTkFrame(caja, fg_color="transparent")

    def alternar_campos():
        if tipo_var.get() == "hongos":
            marco_hongos.pack(fill="x", padx=24, pady=(8, 0))
        else:
            marco_hongos.pack_forget()

    marco_tipo = ctk.CTkFrame(caja, fg_color="transparent")
    marco_tipo.pack(anchor="w", padx=24, pady=(4, 8))
    ctk.CTkRadioButton(marco_tipo, text="Convencional", variable=tipo_var, value="convencional", command=alternar_campos).pack(side="left", padx=(0, 16))
    ctk.CTkRadioButton(marco_tipo, text="Hongos", variable=tipo_var, value="hongos", command=alternar_campos).pack(side="left")

    ctk.CTkLabel(marco_hongos, text="PROPORCIÓN DE LA MEZCLA", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", pady=(12, 2))
    ctk.CTkLabel(marco_hongos, text="Mueve el control para repartir el 100%", font=FUENTE_PEQUENA, text_color=COLOR_SUAVE).pack(anchor="w", pady=(0, 8))
    etiqueta_prop = ctk.CTkLabel(marco_hongos, text="", font=("Segoe UI", 13, "bold"), text_color=COLOR_TEXTO)
    etiqueta_prop.pack(anchor="w", pady=(0, 6))

    def al_mover_deslizador(valor):
        cp = int(round(valor))
        etiqueta_prop.configure(text=f"Cola de pavo  {cp}%          Levadura  {100 - cp}%")

    deslizador = ctk.CTkSlider(marco_hongos, from_=0, to=100, number_of_steps=100, width=340, command=al_mover_deslizador)
    deslizador.set(50)
    deslizador.pack(anchor="w")
    al_mover_deslizador(50)

    ctk.CTkLabel(marco_hongos, text="ZONA DE CULTIVO · COLA DE PAVO", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", pady=(18, 4))
    zona_cola = ctk.StringVar(value="invernadero")
    mzc = ctk.CTkFrame(marco_hongos, fg_color="transparent")
    mzc.pack(anchor="w")
    ctk.CTkRadioButton(mzc, text="Invernadero", variable=zona_cola, value="invernadero").pack(side="left", padx=(0, 12))
    ctk.CTkRadioButton(mzc, text="Casa", variable=zona_cola, value="casa").pack(side="left")

    ctk.CTkLabel(marco_hongos, text="ZONA DE CULTIVO · LEVADURA", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", pady=(16, 4))
    zona_lev = ctk.StringVar(value="invernadero")
    mzl = ctk.CTkFrame(marco_hongos, fg_color="transparent")
    mzl.pack(anchor="w")
    ctk.CTkRadioButton(mzl, text="Invernadero", variable=zona_lev, value="invernadero").pack(side="left", padx=(0, 12))
    ctk.CTkRadioButton(mzl, text="Casa", variable=zona_lev, value="casa").pack(side="left")

    def guardar_nueva():
        nombre = entrada_nombre.get().strip()
        if not nombre:
            return
        es_hongos = tipo_var.get() == "hongos"
        cp = int(round(deslizador.get())) if es_hongos else ""
        baterias.append(crear_ficha_bateria(
            nombre=nombre,
            tipo=tipo_var.get(),
            cola_pavo=str(cp) if es_hongos else "",
            zona_cola_pavo=zona_cola.get() if es_hongos else "",
            levadura=str(100 - cp) if es_hongos else "",
            zona_levadura=zona_lev.get() if es_hongos else "",
        ))
        guardar_datos()
        dlg.destroy()
        seleccionar_bateria(len(baterias) - 1)

    ctk.CTkButton(caja, text="Guardar batería", height=38, command=guardar_nueva).pack(side="bottom", fill="x", padx=24, pady=20)


def accion_eliminar_bateria():
    global seleccionada
    if seleccionada is None:
        messagebox.showinfo("Nada seleccionado", "Primero selecciona una batería.")
        return
    nombre = baterias[seleccionada]["nombre"]
    if messagebox.askyesno("Confirmar eliminación", f"¿Eliminar '{nombre}'? Esta acción no se puede deshacer."):
        baterias.pop(seleccionada)
        seleccionada = None
        guardar_datos()
        refrescar_lista()
        limpiar_panel()


def accion_medir():
    if seleccionada is None:
        messagebox.showinfo("Nada seleccionado", "Primero selecciona una batería.")
        return
    try:
        voltaje, corriente, potencia = leer_medicion_arduino(entrada_puerto.get().strip())
    except ErrorArduino as e:
        messagebox.showerror("Error con el Arduino", str(e))
        return

    baterias[seleccionada].setdefault("mediciones", []).append(
        crear_registro_medicion(voltaje, corriente, potencia))
    guardar_datos()
    actualizar_panel()


def accion_comparar():
    conv = [i for i, b in enumerate(baterias) if b["tipo"] == "convencional"]
    hongos = [i for i, b in enumerate(baterias) if b["tipo"] == "hongos"]

    if not conv or not hongos:
        messagebox.showinfo("Datos insuficientes",
                            "Necesitas al menos una batería convencional "
                            "y una de hongos para comparar.")
        return

    dlg = ctk.CTkToplevel(ventana)
    dlg.title("Comparar baterías")
    dlg.geometry("640x640")
    dlg.configure(fg_color=COLOR_FONDO)
    dlg.transient(ventana)
    dlg.after(100, dlg.grab_set)

    caja = ctk.CTkFrame(dlg, fg_color=COLOR_TARJETA, corner_radius=RADIO_ESQUINA)
    caja.pack(fill="both", expand=True, padx=20, pady=20)

    ctk.CTkLabel(caja, text="Comparar baterías", font=FUENTE_SUBTITULO, text_color=COLOR_TEXTO).pack(anchor="w", padx=24, pady=(20, 16))

    selectores = ctk.CTkFrame(caja, fg_color="transparent")
    selectores.pack(fill="x", padx=24)

    col1 = ctk.CTkFrame(selectores, fg_color="transparent")
    col1.pack(side="left", expand=True, fill="x", padx=(0, 12))
    ctk.CTkLabel(col1, text="CONVENCIONAL", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", pady=(0, 4))
    nombres_conv = [baterias[i]["nombre"] for i in conv]
    sel_conv = ctk.CTkOptionMenu(col1, values=nombres_conv, width=240)
    sel_conv.pack(fill="x")

    col2 = ctk.CTkFrame(selectores, fg_color="transparent")
    col2.pack(side="left", expand=True, fill="x")
    ctk.CTkLabel(col2, text="HONGOS", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", pady=(0, 4))
    nombres_hongos = [baterias[i]["nombre"] for i in hongos]
    sel_hongos = ctk.CTkOptionMenu(col2, values=nombres_hongos, width=240)
    sel_hongos.pack(fill="x")

    ctk.CTkLabel(caja, text="CRITERIO", font=FUENTE_ENCABEZADO, text_color=COLOR_SUAVE).pack(anchor="w", padx=24, pady=(20, 4))
    criterio = ctk.StringVar(value="energia")
    mc = ctk.CTkFrame(caja, fg_color="transparent")
    mc.pack(anchor="w", padx=24)
    ctk.CTkRadioButton(mc, text="Energía total", variable=criterio, value="energia").pack(side="left", padx=(0, 14))
    ctk.CTkRadioButton(mc, text="Vida útil", variable=criterio, value="vida_util").pack(side="left", padx=(0, 14))
    ctk.CTkRadioButton(mc, text="Potencia promedio", variable=criterio, value="potencia").pack(side="left")

    salida = ctk.CTkTextbox(caja, height=280, font=FUENTE_MONO, fg_color=COLOR_CAJA, border_width=0)
    salida.pack(fill="both", expand=True, padx=24, pady=(16, 8))
    salida.configure(state="disabled")

    def ejecutar_comparacion():
        i1 = conv[nombres_conv.index(sel_conv.get())]
        i2 = hongos[nombres_hongos.index(sel_hongos.get())]

        salida.configure(state="normal")
        salida.delete("1.0", "end")

        # Reúne los datos de las dos baterías elegidas
        filas = []
        for i in (i1, i2):
            b = baterias[i]
            med = b.get("mediciones", [])
            filas.append({
                "nombre": b["nombre"],
                "tipo": b["tipo"],
                "mediciones": med,
                "resumen": calcular_resumen(med, b.get("voltaje_corte", 0.30)),
                "pot_prom": calcular_potencia_promedio(med),
            })

        # Muestra el detalle de cada una
        for f in filas:
            tipo = "Hongos" if f["tipo"] == "hongos" else "Convencional"
            salida.insert("end", f"{f['nombre']}  ({tipo})\n")
            if f["mediciones"]:
                u = f["mediciones"][-1]
                salida.insert("end",
                    f"   Última medición:  {u['voltaje_V']:.4f} V   "
                    f"{u['corriente_uA']:.2f} µA   {u['potencia_uW']:.2f} µW\n")
                salida.insert("end", f"   Potencia promedio:  {f['pot_prom']:.2f} µW\n")
            else:
                salida.insert("end", "   Sin mediciones registradas\n")

            if f["resumen"]:
                salida.insert("end",
                    f"   Energía total:  {f['resumen']['energia_uWh']:.2f} µWh\n")
                if f["resumen"]["vida_util_horas"] is not None:
                    salida.insert("end",
                        f"   Vida útil estimada:  {f['resumen']['vida_util_horas']:.2f} h\n")
                else:
                    salida.insert("end", "   Vida útil:  sin tendencia a la baja todavía\n")
            else:
                salida.insert("end", "   Datos insuficientes (se necesitan 2+ mediciones)\n")
            salida.insert("end", "\n")

        # Decide cuál gana según el criterio elegido
        crit = criterio.get()
        candidatos = []
        for f in filas:
            if crit == "energia" and f["resumen"]:
                candidatos.append((f["nombre"], f["resumen"]["energia_uWh"], "µWh"))
            elif crit == "vida_util" and f["resumen"] and f["resumen"]["vida_util_horas"] is not None:
                candidatos.append((f["nombre"], f["resumen"]["vida_util_horas"], "h"))
            elif crit == "potencia" and f["pot_prom"] is not None:
                candidatos.append((f["nombre"], f["pot_prom"], "µW"))

        salida.insert("end", "─" * 50 + "\n")
        if len(candidatos) == 2:
            g = max(candidatos, key=lambda x: x[1])
            salida.insert("end",
                f"Mejor según el criterio elegido:\n   {g[0]}  con {g[1]:.2f} {g[2]}\n")
        else:
            salida.insert("end",
                "No hay datos suficientes en ambas baterías para este criterio.\n")

        salida.configure(state="disabled")

    ctk.CTkButton(caja, text="Comparar", height=38, command=ejecutar_comparacion).pack(fill="x", padx=24, pady=(0, 20))


# 8. ARRANQUE
ctk.CTkButton(cuerpo_arduino, text="Solicitar datos del Arduino", height=34, command=accion_medir).pack(side="left")

ctk.CTkButton(marco_botones, text="+  Nueva batería", height=38, command=accion_nueva_bateria).pack(fill="x", pady=(0, 6))
ctk.CTkButton(marco_botones, text="Comparar baterías", height=38, fg_color=COLOR_BOTON_GRIS, text_color=COLOR_TEXTO, hover_color=COLOR_BOTON_GRIS_HOVER, command=accion_comparar).pack(fill="x", pady=(0, 6))
ctk.CTkButton(marco_botones, text="Eliminar seleccionada", height=38, fg_color="transparent", text_color=COLOR_ROJO, hover_color=COLOR_ROJO_HOVER, command=accion_eliminar_bateria).pack(fill="x")

refrescar_lista()
ventana.mainloop()