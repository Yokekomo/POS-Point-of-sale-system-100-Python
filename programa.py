import os
import pickle
import re
import sqlite3
from functools import partial
from tkinter import *
from tkinter import ttk


# ------------------------------------------ Creamos una ventana principal transparente y sobre ella montamos las otras


class Aplicacion(Frame):
    db_productos = 'BBDD.db'

    def __init__(self, master, *args):
        super().__init__(master, *args)
        self.x = 0
        self.y = 0
        self.x0 = 50
        self.y0 = 50
        self.x1 = 100
        self.y1 = 100
        self.click = True

        self.ventana = Toplevel(self.master)
        self.ventana.overrideredirect(1)
        self.ventana.minsize(width=300, height=200)
        self.ventana.geometry('640x480+300+50')

        # ------------------------------------------------------------------------------------------------ Barra titulo

        self.frame_top = Frame(self.ventana, bg='grey', height=30)
        self.frame_top.grid_propagate(0)
        self.frame_top.grid(row=0, column=0, sticky='nsew')

        # --------------------------------------------------------------------------------------------- Frame contenido

        self.frame_principal = Frame(self.ventana, bg='#4b4b4b')
        self.frame_principal.grid(row=1, column=0, sticky='nsew')

        self.ventana.columnconfigure(0, weight=1)
        self.ventana.rowconfigure(1, weight=1)

        self.frame_principal.columnconfigure(0, weight=1)
        self.frame_principal.columnconfigure(1, weight=1)
        self.frame_principal.columnconfigure(2, weight=1)
        self.frame_principal.rowconfigure(0, weight=1)
        self.frame_principal.rowconfigure(1, weight=1)

        self.frame_top.bind("<ButtonPress-1>", self.start)
        self.frame_top.bind("<B1-Motion>", self.mover)
        self.master.bind("<Map>", self.on_deiconify)
        self.master.bind("<Unmap>", self.on_iconify)

        self.grid = ttk.Sizegrip(self.frame_principal, style="TSizegrip")
        self.grid.place(relx=1.0, rely=1.0, anchor="se")
        self.grid.bind("<B1-Motion>", self.redimensionar)
        ttk.Style().configure("TSizegrip", "#4b4b4b")

        self.widgets()

    # --------------------------------------------------------------------------------------------------- Redimensionar

    def redimensionar(self, event):
        self.x1 = self.ventana.winfo_pointerx()
        self.y1 = self.ventana.winfo_pointery()
        self.x0 = self.ventana.winfo_rootx()
        self.y0 = self.ventana.winfo_rooty()
        print(self.x0, self.y0, self.x1, self.y1)
        try:
            self.ventana.geometry("%sx%s" % ((self.x1 - self.x0), (self.y1 - self.y0)))
        except:
            pass

    # ----------------------------------------------------------------------------------------------------------- Salir

    def salir(self):
        self.ventana.destroy()
        self.ventana.quit()

    def salir2(self):
        self.ventana_productos.destroy()

    # ----------------------------------------------------------------------------------------- Mover Ventana principal

    def start(self, event):
        self.x = event.x
        self.y = event.y

    def mover(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y

        if self.ventana.winfo_y() > 0:
            self.ventana.geometry("+%s+%s" % (self.ventana.winfo_x() + deltax, self.ventana.winfo_y() + deltay))
            self.ventana.update()

        elif self.ventana.winfo_y() <= 1:
            self.ventana.geometry("+%s+%s" % (self.ventana.winfo_x() + deltax, self.ventana.winfo_y() + deltay))
            self.ventana.update()
            self.pantalla_completa()
            self.cambiar_tamanyo.config(text='Minimizar')
            self.click = False

            if (self.ventana.winfo_y() <= 50) and (self.ventana.winfo_y() > 0):
                self.click = True
                self.cambiar_tamanyo.config(text='Maximizar')
                self.ventana.geometry("%sx%s+%s+%s" % ((self.x1 - self.x0),
                                                       (self.y1 - self.y0),
                                                       (self.x0 - self.y0),
                                                       (self.x1 - self.y1)))
                self.ventana.geometry("+%s+%s" % (self.ventana.winfo_x() + deltax, self.ventana.winfo_y() + deltay))
                self.ventana.update()

    # ----------------------------------------------------------------------------------- Dimensionar ventana principal

    def pantalla_completa(self):
        self.ventana.geometry("{0}x{1}+0+0".format(self.ventana.winfo_screenwidth(),
                                                   self.ventana.winfo_screenheight() - 30))

    def cambiar_dimension(self):

        if self.click:
            self.cambiar_tamanyo.config(text='Minimizar')
            self.pantalla_completa()
            self.click = False
        else:
            self.cambiar_tamanyo.config(text='Maximizar')
            self.ventana.geometry("%sx%s+%s+%s" % ((self.x1 - self.x0),
                                                   (self.y1 - self.y0),
                                                   (self.x0 - self.y0),
                                                   (self.x1 - self.y1)))
            self.click = True

    def on_deiconify(self, event):
        self.ventana.wm_deiconify()
        self.master.lower()

    def on_iconify(self, event):
        self.ventana.withdraw()
        self.master.iconify()

    # --------------------------------------------------------------------------------------------------- Conectar BBDD

    def mi_conexion(self, query, parameters=()):
        with sqlite3.connect(self.db_productos) as mi_conexion:
            mi_cursor = mi_conexion.cursor()
            datos_respuesta = mi_cursor.execute(query, parameters)
            mi_conexion.commit()
        return datos_respuesta

    # ----------------------------------------------------------------------------------------- Crear BBDD si no existe

    def conexionBBDD(self):
        mi_conexion = sqlite3.connect('BBDD.db')
        mi_cursor = mi_conexion.cursor()

        try:
            mi_cursor.execute('''
                CREATE TABLE DATOSPRODUCTOS (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                PRODUCTO VARCHAR(15),
                PRECIO VARCHAR(5),
                TAX VARCHAR(5),
                STOCK VARCHAR(5),
                FAMILIA VARCHAR(15))
                ''')
            self.ventana_advertencia('BBDD creada con éxito')
        except:
            pass

    # ------------------------------------------------------------------------------------------ Función Crear Producto

    def crear_producto(self, producto, precio, tax, stock, familia):
        query = f'INSERT INTO DATOSPRODUCTOS VALUES(NULL,?,?,?,?,?)'
        self.mi_conexion(query, (producto, precio, tax, stock, familia))
        self.ventana_advertencia('Registro insertado con éxito')
        self.actualizar_datos()

    # ----------------------------------------------------------------------------------------- Función Buscar Producto

    def leer_producto(self, id_producto):
        query = f"SELECT * FROM DATOSPRODUCTOS WHERE ID= '{id_producto}'"
        el_producto = self.mi_conexion(query)
        for elemento in el_producto:
            self.id_producto.set(elemento[0])
            self.producto.set(elemento[1])
            self.precio.set(elemento[2])
            self.tax.set(elemento[3])
            self.stock.set(elemento[4])
            self.familia.set(elemento[5])

    # ------------------------------------------------------------------------------------- Función Actualizar Producto

    def modificar_producto(self, id_producto, producto, precio, tax, stock, familia):
        query = f'UPDATE DATOSPRODUCTOS SET PRODUCTO=?, PRECIO=?, TAX=?, STOCK=?, FAMILIA=?' + 'WHERE ID=?'
        self.mi_conexion(query, (producto, precio, tax, stock, familia, id_producto))
        self.ventana_advertencia('Registro actualizado con éxito')
        self.actualizar_datos()

    # ----------------------------------------------------------------------------------------- Función Borrar Producto

    def borrar_producto(self, id_producto):
        query = f'DELETE FROM DATOSPRODUCTOS WHERE ID=?'
        self.mi_conexion(query, (id_producto,))
        self.ventana_advertencia('Registro borrado con éxito')
        self.actualizar_datos()

    # -------------------------------------------------------------------------------- Actualizar datos Tabla Productos

    def actualizar_datos(self):
        datos_tabla = self.tabla.get_children()
        for elementos in datos_tabla:
            self.tabla.delete(elementos)

        query = 'SELECT * FROM DATOSPRODUCTOS ORDER BY FAMILIA DESC'
        respuesta_consulta = self.mi_conexion(query)

        for caja in respuesta_consulta:
            self.tabla.insert('', 0, text=caja[0], values=(caja[1], caja[2], caja[3], caja[4], caja[5]))

    # ----------------------------------------------------------------------------------- Limpiar Treeview Cuenta Orden

    def limpiar_campos(self):
        campos = self.grid.get_children()
        for productos in campos:
            self.grid.delete(productos)
        campos2 = self.total.get_children()
        for productos in campos2:
            self.total.delete(productos)

    def limpiar_formulario(self):

        self.id_producto.set('')
        self.producto.set('')
        self.precio.set('')
        self.tax.set('')
        self.stock.set('')
        self.familia.set('')

    # ---------------------------------------------------------------------- Función para seleccionar desde el Treeview

    def datos_tabla(self, event):
        los_datos = self.tabla.focus()
        if not los_datos:
            return
        datos = self.tabla.item(los_datos)
        id_producto = datos["text"]
        producto, precio, tax, stock, familia = datos["values"]

        self.id_producto.set(id_producto)
        self.producto.set(producto)
        self.precio.set(precio)
        self.tax.set(tax)
        self.stock.set(stock)
        self.familia.set(familia)

    # -------------------------------------------------------------------------------------- Ventana Opciones productos

    def funcion_opciones(self):

        self.ventana_productos = Tk()
        self.ventana_productos.wm_title('Productos')

        # ---------------------------------------------------------------------------------------------------- Barra titulo

        self.frame_top2 = Frame(self.ventana_productos, bg='grey', height=30)
        self.frame_top2.grid_propagate(0)
        self.frame_top2.grid(row=0, column=0, sticky='nsew')

        # ------------------------------------------------------------------------------------------------- Frame contenido

        self.frame_ventana_productos = Frame(self.ventana_productos, bg='#4b4b4b')
        self.frame_ventana_productos.grid(row=1, column=0, sticky='nsew')

        self.ventana_productos.columnconfigure(0, weight=1)
        self.ventana_productos.rowconfigure(1, weight=1)

        self.frame_ventana_productos.columnconfigure(0, weight=1)
        self.frame_ventana_productos.columnconfigure(1, weight=1)
        self.frame_ventana_productos.columnconfigure(2, weight=1)
        self.frame_ventana_productos.rowconfigure(0, weight=1)
        self.frame_ventana_productos.rowconfigure(1, weight=1)

        self.cerrar = Button(self.frame_top2, text='Cerrar',
                             bg='grey', activebackground='grey', bd=0, command=self.salir2)
        self.cerrar.pack(ipadx=5, padx=5, side='right', ipady=2)

        ancho_ventana = 632
        alto_ventana = 308

        x_ventana = self.ventana_productos.winfo_screenwidth() // 2 - ancho_ventana // 2
        y_ventana = self.ventana_productos.winfo_screenheight() // 2 - alto_ventana // 2

        posicion = str(ancho_ventana) + "x" + str(alto_ventana) + "+" + str(x_ventana) + "+" + str(y_ventana)
        self.ventana_productos.geometry(posicion)

        self.ventana_productos.resizable(0, 0)
        self.ventana_productos.overrideredirect(1)
        self.ventana_productos.config(bg='#4b4b4b')

        # ------------------------------------------------------------------------ Label y Entry ventana opciones productos

        cuadro = Frame(self.ventana_productos, bg='grey')
        cuadro.place(x=2, y=32, width=226, height=200)

        cuadro_interno = Frame(self.ventana_productos, bg='#424242')
        cuadro_interno.place(x=8, y=38, width=215, height=189)

        self.id_producto = StringVar(self.ventana_productos)
        self.producto = StringVar(self.ventana_productos)
        self.precio = StringVar(self.ventana_productos)
        self.tax = StringVar(self.ventana_productos)
        self.stock = StringVar(self.ventana_productos)
        self.familia = StringVar(self.ventana_productos)

        self.cuadro_id = Entry(cuadro_interno, textvariable=self.id_producto)
        self.cuadro_id.grid(row=0, column=1, padx=8, pady=5)

        self.cuadro_producto = Entry(cuadro_interno, textvariable=self.producto)
        self.cuadro_producto.grid(row=1, column=1, padx=8, pady=5)

        self.cuadro_precio = Entry(cuadro_interno, textvariable=self.precio)
        self.cuadro_precio.grid(row=2, column=1, padx=8, pady=5)

        self.cuadro_tax = Entry(cuadro_interno, textvariable=self.tax)
        self.cuadro_tax.grid(row=3, column=1, padx=8, pady=5)

        self.cuadro_stock = Entry(cuadro_interno, textvariable=self.stock)
        self.cuadro_stock.grid(row=4, column=1, padx=8, pady=5)

        self.cuadro_familia = Entry(cuadro_interno, textvariable=self.familia)
        self.cuadro_familia.grid(row=5, column=1, padx=8, pady=5)

        self.label_id = Label(cuadro_interno, text='Id:', background='#424242', fg='white')
        self.label_id.grid(row=0, column=0, sticky='w', padx=8, pady=5)

        self.label_producto = Label(cuadro_interno, text='Producto:', background='#424242', fg='white')
        self.label_producto.grid(row=1, column=0, sticky='w', padx=8, pady=5)

        self.label_precio = Label(cuadro_interno, text='Precio:', background='#424242', fg='white')
        self.label_precio.grid(row=2, column=0, sticky='w', padx=8, pady=5)

        self.label_tax = Label(cuadro_interno, text='Impuesto:', background='#424242', fg='white')
        self.label_tax.grid(row=3, column=0, sticky='w', padx=8, pady=5)

        self.label_stock = Label(cuadro_interno, text='Cantidad:', background='#424242', fg='white')
        self.label_stock.grid(row=4, column=0, sticky='w', padx=8, pady=5)

        self.label_familia = Label(cuadro_interno, text='Familia:', background='#424242', fg='white')
        self.label_familia.grid(row=5, column=0, sticky='w', padx=8, pady=5)

        # ------------------------------------------------------------------------------ Botones ventana opciones productos

        cuadro2 = Frame(self.ventana_productos, bg='grey')
        cuadro2.place(x=2, y=235, width=226, height=70)

        btn_guardar_producto = Button(cuadro2, text='Guardar',
                                      command=lambda: self.crear_producto(self.producto.get(),
                                                                          self.precio.get(),
                                                                          self.tax.get(),
                                                                          self.stock.get(),
                                                                          self.familia.get()),
                                      bg='#424242', fg='white')
        btn_guardar_producto.place(x=6, y=2, width=70, height=30)

        btn_buscar_producto = Button(cuadro2, text='Buscar',
                                     command=lambda: self.leer_producto(self.id_producto.get()),
                                     bg='#424242',
                                     fg='white')
        btn_buscar_producto.place(x=78, y=2, width=70, height=30)

        btn_guardar_producto = Button(cuadro2, text='Modificar',
                                      command=lambda: self.modificar_producto(self.id_producto.get(),
                                                                              self.producto.get(),
                                                                              self.precio.get(),
                                                                              self.tax.get(),
                                                                              self.stock.get(),
                                                                              self.familia.get()),
                                      bg='#424242',
                                      fg='white')
        btn_guardar_producto.place(x=150, y=2, width=70, height=30)

        btn_limpiar_campos = Button(cuadro2, text='Limpiar Campos',
                                    command=lambda: self.limpiar_formulario(),
                                    bg='#424242',
                                    fg='white')
        btn_limpiar_campos.place(x=6, y=34, width=106, height=30)

        btn_limpiar_campos = Button(cuadro2, text='Borrar Producto',
                                    command=lambda: self.ventana_confirmar('¿Desea borrar este producto?',
                                                                           self.id_producto.get()),
                                    bg='#424242',
                                    fg='red')
        btn_limpiar_campos.place(x=114, y=34, width=106, height=30)

        # ------------------------------------------------------------------------------------- Tabla Información productos

        cuadro3 = Frame(self.ventana_productos, bg='grey', highlightbackground='grey', highlightthickness=2)
        cuadro3.place(x=230, y=32, width=400, height=273)

        self.tabla = ttk.Treeview(cuadro3, columns=('col1', 'col2', 'col3', 'col4', 'col5'))

        barra_desplazamiento = ttk.Scrollbar(cuadro3)
        barra_desplazamiento.configure(command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=barra_desplazamiento.set)
        barra_desplazamiento.pack(side=RIGHT, fill=BOTH)
        self.tabla.pack(side=TOP, fill=BOTH, expand=TRUE)

        self.tabla.column('#0', width=1, anchor=W)
        self.tabla.column('col1', width=35, anchor=CENTER)
        self.tabla.column('col2', width=20, anchor=CENTER)
        self.tabla.column('col3', width=2, anchor=CENTER)
        self.tabla.column('col4', width=3, anchor=CENTER)
        self.tabla.column('col5', width=25, anchor=CENTER)

        self.tabla.heading('#0', text='Id', anchor=W)
        self.tabla.heading('col1', text='Producto', anchor=CENTER)
        self.tabla.heading('col2', text='Precio', anchor=CENTER)
        self.tabla.heading('col3', text='Tax', anchor=CENTER)
        self.tabla.heading('col4', text='Stock', anchor=CENTER)
        self.tabla.heading('col5', text='Familia', anchor=CENTER)

        # ------------------------------------------------------------ Cargar Tabla en ventana opciones con datos Productos

        self.actualizar_datos()

        self.tabla.bind("<<TreeviewSelect>>", self.datos_tabla)

    # ---------------------------------------------------------------------------- Botones Barra ventana principal Menu

    def widgets(self):

        self.cerrar = Button(self.frame_top, text='Cerrar',
                             bg='grey', activebackground='grey', bd=0, command=self.salir)
        self.cerrar.pack(ipadx=5, padx=5, side='right', ipady=2)

        self.cambiar_tamanyo = Button(self.frame_top, text='Maximizar',
                                      bg='grey', activebackground='grey', bd=0, command=self.cambiar_dimension)
        self.cambiar_tamanyo.pack(ipadx=5, padx=5, side='right')

        self.minimizar = Button(self.frame_top, text='Minimizar',
                                bg='grey', activebackground='grey', bd=0, command=lambda: self.master.iconify())
        self.minimizar.pack(ipadx=5, padx=5, side='right')

        self.menu = Button(self.frame_top, text='Opciones',
                           bg='grey', activebackground='grey', bd=0, command=self.funcion_opciones)
        self.menu.pack(ipadx=5, padx=5, side='right', ipady=2)

        self.titulo = Label(self.frame_top, text='TPV GRATIS', bg='grey', fg='black', font=('Arial', 12))
        self.titulo.pack(padx=10, side='left')
        self.titulo.bind("<B1-Motion>", self.mover)
        self.titulo.bind("<ButtonPress-1>", self.start)

        # ------------------------------------------------------------------------------------------------------- Frame Uno

        frame_uno = Frame(self.frame_principal, bg='#424242', width=100, height=300,
                          highlightbackground='grey', highlightthickness=2)
        frame_uno.grid(padx=2, pady=2, column=0, columnspan=1, row=0, sticky='nsew')

        # --------------------------------------------------------------------------- Treeview para productos seleccionados

        self.grid = ttk.Treeview(frame_uno, columns=('col1', 'col2', 'col3', 'col4'))
        self.grid.pack(side=TOP, fill=BOTH, expand=TRUE)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
                        background="#424242",
                        foreground="grey",
                        rowheight=25,
                        fieldbackground="#424242"
                        )
        style.map('Treeview',
                  background=[('selected', '#4b4b4b')])

        self.grid.column('#0', width=2)
        self.grid.column('col1', width=55, anchor=CENTER)
        self.grid.column('col2', width=12, anchor=CENTER)
        self.grid.column('col3', width=5, anchor=CENTER)
        self.grid.column('col4', width=5, anchor=CENTER)

        self.grid.heading('#0', text='id', anchor=CENTER)
        self.grid.heading('col1', text='Producto', anchor=CENTER)
        self.grid.heading('col2', text='Cant.', anchor=CENTER)
        self.grid.heading('col3', text='tax', anchor=CENTER)
        self.grid.heading('col4', text='Precio', anchor=CENTER)

        self.total = ttk.Treeview(frame_uno, height=1)
        self.total.pack(side=RIGHT, fill=BOTH, expand=TRUE)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
                        background="#424242",
                        foreground="grey",
                        rowheight=25,
                        fieldbackground="#424242"
                        )
        style.map('Treeview',
                  background=[('selected', '#4b4b4b')])

        # -------------------------------------------------------------------------------------- Treeview para total precio

        self.total.column('#0', width=25)
        self.total.heading('#0', text='Total', anchor=CENTER)

        # --------------------------------------------------------------------------------- Botones Imprimir Guardar Cobrar

        btn_imprimir = Button(frame_uno, text='Imprimir', bg='#424242', fg='white', command=self.imprimir_ticket)
        btn_imprimir.pack(side=LEFT, fill=BOTH, expand=TRUE)

        btn_Guardar = Button(frame_uno, text='Guardar', bg='#424242', fg='white', command=self.guardar_ticket)
        btn_Guardar.pack(side=LEFT, fill=BOTH, expand=TRUE)

        btn_Cobrar = Button(frame_uno, text='Cobrar', bg='#424242', fg='white', command=self.ticket_en_pantalla)
        btn_Cobrar.pack(side=LEFT, fill=BOTH, expand=TRUE)

        # ------------------------------------------------------------------------------------------------------- Frame Dos

        self.frame_dos = Frame(self.frame_principal, bg='#424242', width=100, height=340,
                               highlightbackground='grey', highlightthickness=2)
        self.frame_dos.grid(padx=2, pady=2, column=1, columnspan=4, row=0, sticky='nswe')

        # ------------------------------------------------------------------------------------------------------ Frame Tres

        frame_tres = Frame(self.frame_principal, bg='#424242', width=100, height=340,
                           highlightbackground='grey', highlightthickness=2)
        frame_tres.grid(padx=2, pady=2, column=5, row=0, columnspan=2, sticky='nsew')

        Grid.rowconfigure(frame_tres, 0, weight=1)
        Grid.columnconfigure(frame_tres, 0, weight=1)

        ventana = Frame(frame_tres)
        ventana.grid(row=0, column=0, sticky=N)

        # ------------------------------------------------------------------------ Busca las familias existentes en la BBDD

        try:
            query = (f"SELECT FAMILIA FROM DATOSPRODUCTOS WHERE FAMILIA=" + 'FAMILIA')
            mi_cursor = self.mi_conexion(query)
            fila = mi_cursor.fetchall()
        except IndexError:
            pass

        # ----------------------------------------------------------------- Ordena y elimina los items repetidos en familia

        lista_limpia = []
        lista_unicos = []

        num = 0

        try:
            while len(lista_limpia) != range(len(fila)):
                for x in fila[num]:
                    x = str(x)
                    lista_limpia.append(x)
                    num += 1
                    for element in lista_limpia:
                        if element not in lista_unicos:
                            lista_unicos.append(element)
        except IndexError:
            pass

        numero = 0
        lista_ordenada = sorted(lista_unicos)

        # -------------------------------------------------------- Crea botones para cada familia y los carga en frame tres

        try:
            for row_index in range(len(lista_ordenada)):
                Grid.rowconfigure(ventana, row_index, weight=1)
                for col_index in range(1):
                    Grid.columnconfigure(ventana, col_index, weight=0)
                    boton = Button(ventana, text=lista_ordenada[numero], height=1, width=10, bg='#424242',
                                   fg='white', activebackground='grey')

                    boton.configure(command=partial(self.efecto_boton, lista_ordenada[numero], lista_ordenada, 1))
                    boton.grid(row=row_index, column=col_index, sticky=N + S + E + W)
                    numero += 1
        except IndexError:
            pass

        # ---------------------------------------------------------------------------------------------------- Frame Cuatro

        self.frame_cuatro = Frame(self.frame_principal, bg='#424242', width=900, height=10,
                                  highlightbackground='grey', highlightthickness=2)
        self.frame_cuatro.grid(padx=2, pady=2, column=0, row=1, columnspan=7, sticky='nsew')

    # ---------------------------------------------------------------------------------- Cargar ordenes en frame cuatro

    def cargar_ordenes(self, dato):

        Grid.rowconfigure(self.frame_cuatro, 0, weight=1)
        Grid.columnconfigure(self.frame_cuatro, 0, weight=1)

        self.enumerador_columnas_ordenes(dato)
        archivo = open("columnas.txt", "a+")
        archivo.seek(0)
        numero_columna = archivo.readline()
        extraer_numero = [int(s) for s in re.findall(r'-?\d+\.?\d*', dato)]
        numero = "".join(map(str, extraer_numero))

        ventana = Frame(self.frame_cuatro, background='#424242')
        ventana.grid(row=0, column=int(numero_columna), sticky='nsew')
        lista = []
        lista.append(numero)

        try:

            Grid.rowconfigure(ventana, 1, weight=1)
            Grid.columnconfigure(ventana, 1, weight=0)
            boton = Button(ventana, text=f' Ticket nº {numero} \n\nPresione\npara\nCobrar', height=0, width=0,
                           bg='#424242', fg='white', activebackground='grey',
                           command=lambda: [self.efecto_boton(numero, lista, 3), boton.destroy()])
            boton.grid(row=1, column=int(numero_columna), sticky=N + S + E + W)

        except:
            print('Error al cargar la orden al frame cuatro')

        print(numero)
        print(lista)

        self.limpiar_campos()

    # --------------------------------------- Busca los items de la familia elegida y Cargar los productos en frame dos

    def menu_productos(self, dato):

        try:
            query = f'SELECT PRODUCTO FROM DATOSPRODUCTOS WHERE FAMILIA=?'
            mi_cursor = self.mi_conexion(query, (dato,))
            fila = mi_cursor.fetchall()
        except IndexError:
            pass

    # ------------------------------------------------------------------------------------- Elimina productos repetidos

        lista_limpia = []
        lista_unicos = []

        num = 0

        try:
            while len(lista_limpia) != range(len(fila)):
                for x in fila[num]:
                    x = str(x)
                    lista_limpia.append(x)
                    num += 1
                    for element in lista_limpia:
                        if element not in lista_unicos:
                            lista_unicos.append(element)
        except IndexError:
            pass

        numero = 0
        lista_ordenada = sorted(lista_unicos)
        numero_columna = 0
        numero_fila = 0

    # ------------------------------------------------------------------------- Crea boton por cada producto encontrado

        Grid.rowconfigure(self.frame_dos, 0, weight=1)
        Grid.columnconfigure(self.frame_dos, 0, weight=1)
        ventana = Frame(self.frame_dos, background='#424242')
        ventana.grid(row=numero_fila, column=numero_columna, sticky='nsew')
        try:
            for fila in range(10):
                Grid.rowconfigure(ventana, int(numero_fila), weight=0)
                for columna in range(10):
                    Grid.columnconfigure(ventana, int(numero_columna), weight=1)
                    boton = Button(ventana, text=lista_ordenada[numero], height=0, width=1, bg='#424242',
                                   fg='white', activebackground='grey')
                    boton.configure(command=partial(self.efecto_boton, lista_ordenada[numero], lista_ordenada, 2))
                    boton.grid(row=numero_fila, column=numero_columna, ipadx=35, ipady=10, sticky=N + S + E + W)
                    numero += 1
                    numero_columna += 1
                    if numero_columna == 8:
                        numero_fila += 1
                        numero_columna = 0

        except IndexError:
            pass

    # ------------------------------------------------------------- Busca en la BBDD el producto para sacar información

    def enviar_producto_cuenta(self, dato):

        query = f'SELECT * FROM DATOSPRODUCTOS WHERE PRODUCTO=?'
        mi_cursor = self.mi_conexion(query, (dato,))
        el_producto = mi_cursor.fetchall()

        valor = 1

        try:
            for productos in el_producto:
                self.grid.insert('', 0, text=productos[0], values=(productos[1], valor, productos[3], productos[2]))

        except IndexError:
            pass

    # ----------------------------------------------------- Envía la suma del precio de los productos al Treeview Total

        total_suma = 0
        for item in self.grid.get_children():
            celda = float(self.grid.set(item, 'col4'))
            total_suma += celda

        total_suma_print = [str('{0:.2f}'.format(total_suma))]

        try:
            self.total.insert('', 0, text=total_suma_print[0])
        except IndexError:
            pass

    # ----------------------------------------------------------------------------------- Función botón imprimir ticket

    def imprimir_ticket(self):

        total_productos = []
        for item in self.grid.get_children():
            celda = self.grid.set(item, 'col2')
            total_productos.append(celda)

        if len(total_productos) == 0:
            self.ventana_advertencia('El ticket no contiene productos')

        else:
            total_productos = []
            for item in self.grid.get_children():
                celda = self.grid.set(item, 'col1')
                total_productos.append(celda)

            print('Esta parte esta en construcción')
            self.guardado_automatico_ticket(total_productos)

    # ------------------------------------------------------------------------------------ Función botón guardar ticket

    def guardar_ticket(self):

        total_productos = []
        for item in self.grid.get_children():
            celda = self.grid.set(item, 'col2')
            total_productos.append(celda)

        if len(total_productos) == 0:
            self.ventana_advertencia('El ticket no contiene productos')

        else:
            total_productos = []
            for item in self.grid.get_children():
                celda = self.grid.set(item, 'col1')
                total_productos.append(celda)

            self.guardado_automatico_ticket(total_productos)

    # ----------------------------------------------------------------------------- Sistema enumerador columnas ordenes

    def enumerador_columnas_ordenes(self, dato):

        archivo = open("columnas.txt", "a+")
        archivo.seek(0)
        numero = archivo.readline()
        if len(numero) == 0:
            numero = "0"
            archivo.write(numero)
        elif len(numero) == 2:
            archivo = open("columnas.txt", "w+")
            archivo.seek(0)
            numero = "0"
            archivo.write(numero)
            archivo.close()
            self.ventana_advertencia('Ventana Ordenes llena\nCobre una orden')
        archivo.close()
        try:
            columna_orden = int(numero)
            columna_orden += 1
            archivo = open("columnas.txt", "w")
            archivo.write(str(columna_orden))
            archivo.close()
        except:
            self.ventana_advertencia('Error en contador columnas ordenes')

    # --------------------------------------------------------------------------------------- Sistema enumerador ticket

    def guardado_automatico_ticket(self, dato):

        archivo = open("ticket_numero.txt", "a+")
        archivo.seek(0)
        numero = archivo.readline()
        if len(numero) == 0:
            numero = "1"
            archivo.write(numero)
        archivo.close()
        try:
            ticket = int(numero)
            ticket += 1
            archivo = open("ticket_numero.txt", "w")
            archivo.write(str(ticket))
            archivo.close()
        except:
            self.ventana_advertencia('Error en contador ticket')

    # ---------------------------------------------------------------------------------------- Guarda ticket en archivo

        archivo = open("ticket_numero.txt", "a+")
        archivo.seek(0)
        ticket_numero = archivo.readline()
        archivo.close()

        try:
            if f'tickets/lista.pickle{ticket_numero}' != os.pardir:
                with open(f'tickets/lista{ticket_numero}.pickle', 'wb') as archivo:
                    pickle.dump(dato, archivo)
                    dato = 'f\'' + f'tickets/lista.pickle{ticket_numero}' + '\''
                    archivo.close()
                    self.cargar_ordenes(dato)
                    self.ventana_advertencia(f'Orden número {ticket_numero} guardada')

        except:
            self.ventana_advertencia(f'Ticket no guardado {ticket_numero}')

    # -------------------------------------------------------------------------------------- Abre ventana Cobrar ticket

    def cobrar_ticket(self, info_productos, info_precios, total_suma_print):

        total_productos = []
        for item in self.grid.get_children():
            celda = self.grid.set(item, 'col2')
            total_productos.append(celda)

        if len(total_productos) == 0:
            self.ventana_advertencia('El ticket no contiene productos')

        else:
            self.ventana_pago = Tk()
            self.ventana_pago.configure(background='#424242')
            self.ventana_pago.overrideredirect(1)

            ancho_ventana = 250
            alto_ventana = 338

            x_ventana = self.ventana_pago.winfo_screenwidth() // 2 - ancho_ventana // 2
            y_ventana = self.ventana_pago.winfo_screenheight() // 2 - alto_ventana // 2

            posicion = str(ancho_ventana) + "x" + str(alto_ventana) + "+" + str(x_ventana) + "+" + str(y_ventana)
            self.ventana_pago.geometry(posicion)

            self.ventana_pago.resizable(0, 0)

            cuadro = Frame(self.ventana_pago, background='grey')
            cuadro.place(x=3, y=3, width=244, height=294)

            cuadro_interno = Frame(cuadro, background='#4b4b4b')
            cuadro_interno.place(x=2, y=2, width=240, height=290)

            cuadro_interno_productos = Frame(cuadro, background='#4b4b4b')
            cuadro_interno_productos.place(x=20, y=20, width=130, height=252)

            cuadro_interno_precios = Frame(cuadro, background='#4b4b4b')
            cuadro_interno_precios.place(x=160, y=20, width=60, height=252)

            aviso_texto = Label(cuadro_interno, text='PRODUCTOS\t\tPRECIOS', fg='white', background='#4b4b4b')
            aviso_texto.pack(side=TOP)

            aviso_texto1 = Label(cuadro_interno_productos, text=info_productos, fg='white',
                                 background='#4b4b4b', justify=LEFT)
            aviso_texto1.pack(side=LEFT)

            aviso_texto2 = Label(cuadro_interno_precios, text=info_precios, fg='white',
                                 background='#4b4b4b', justify=RIGHT)
            aviso_texto2.pack(side=RIGHT)

            aviso_texto3 = Label(cuadro_interno, text=f'TOTAL = {total_suma_print}€', fg='white',
                                 background='#4b4b4b')
            aviso_texto3.pack(side=BOTTOM)

            cuadro2 = Frame(self.ventana_pago, background='grey')
            cuadro2.place(x=3, y=300, width=244, height=34)

            cuadro_interno2 = Frame(cuadro2, background='#4b4b4b')
            cuadro_interno2.place(x=2, y=2, width=240, height=30)

            btn_pago_efectivo = Button(cuadro_interno2, background='#424242', text='Efectivo', fg='white',
                                       command=self.pago_efectivo)
            btn_pago_efectivo.place(x=3, y=3, width=77, height=25)

            btn_pago_efectivo = Button(cuadro_interno2, background='#424242', text='Tarjeta', fg='white',
                                       command=self.pago_tarjeta)
            btn_pago_efectivo.place(x=82, y=3, width=77, height=25)

            btn_pago_efectivo = Button(cuadro_interno2, background='#424242', text='Cancelar', fg='white',
                                       command=self.pago_cancelar)
            btn_pago_efectivo.place(x=161, y=3, width=77, height=25)

            self.ventana_pago.mainloop()

    def pago_efectivo(self):
        self.ventana_pago.destroy()

    def pago_tarjeta(self):
        self.ventana_pago.destroy()

    def pago_cancelar(self):
        self.ventana_pago.destroy()

    # --------------------------------------------------------------------------------------------- Ventana Advertencia

    def ventana_advertencia(self, aviso):

        ventana_advertencia = Tk()
        ventana_advertencia.configure(background='#424242')
        ventana_advertencia.overrideredirect(1)

        ancho_ventana = 200
        alto_ventana = 100

        x_ventana = ventana_advertencia.winfo_screenwidth() // 2 - ancho_ventana // 2
        y_ventana = ventana_advertencia.winfo_screenheight() // 2 - alto_ventana // 2

        posicion = str(ancho_ventana) + "x" + str(alto_ventana) + "+" + str(x_ventana) + "+" + str(y_ventana)
        ventana_advertencia.geometry(posicion)

        ventana_advertencia.resizable(0, 0)

        cuadro = Frame(ventana_advertencia, background='grey')
        cuadro.place(x=0, y=0, width=200, height=100)

        cuadro_interno = Frame(cuadro, background='#4b4b4b')
        cuadro_interno.place(x=2, y=2, width=196, height=96)

        aviso_texto = Label(cuadro_interno, text=aviso, fg='white', background='#4b4b4b')
        aviso_texto.pack(ipady=15, side=TOP)

        btn_aceptar = Button(cuadro, activebackground='grey', background='#424242', text='Cerrar aviso', fg='white',
                             command=lambda: ventana_advertencia.destroy())
        btn_aceptar.place(x=60, y=60, width=80, height=25)

        ventana_advertencia.mainloop()

    # -------------------------------------------------------------------------------------------- Ventana Confirmación

    def ventana_confirmar(self, aviso, id_producto):

        ventana_confirmar = Tk()
        ventana_confirmar.configure(background='#424242')
        ventana_confirmar.overrideredirect(1)

        ancho_ventana = 200
        alto_ventana = 100

        x_ventana = ventana_confirmar.winfo_screenwidth() // 2 - ancho_ventana // 2
        y_ventana = ventana_confirmar.winfo_screenheight() // 2 - alto_ventana // 2

        posicion = str(ancho_ventana) + "x" + str(alto_ventana) + "+" + str(x_ventana) + "+" + str(y_ventana)
        ventana_confirmar.geometry(posicion)

        ventana_confirmar.resizable(0, 0)

        cuadro = Frame(ventana_confirmar, background='grey')
        cuadro.place(x=0, y=0, width=200, height=100)

        cuadro_interno = Frame(cuadro, background='#4b4b4b')
        cuadro_interno.place(x=2, y=2, width=196, height=96)

        aviso_texto = Label(cuadro_interno, text=aviso, fg='white', background='#4b4b4b')
        aviso_texto.pack(ipady=15, side=TOP)

        btn_aceptar = Button(cuadro, activebackground='grey', background='#424242', text='Borrar', fg='red',
                             command=lambda: [ventana_confirmar.destroy(), self.borrar_producto(id_producto)])
        btn_aceptar.place(x=18, y=60, width=80, height=25)

        btn_cancelar = Button(cuadro, activebackground='grey', background='#424242', text='Cancelar', fg='white',
                              command=lambda: ventana_confirmar.destroy())
        btn_cancelar.place(x=102, y=60, width=80, height=25)

        ventana_confirmar.mainloop()

    # --------------------------------------------------------------------------  Impresión del Ticket en Ventana Cobro

    def ticket_en_pantalla(self):

        total_productos = []
        for item in self.grid.get_children():
            celda = self.grid.set(item, 'col1')
            total_productos.append(celda)

        lista_items_precios = []
        for elemento in total_productos:
            query = f"SELECT * FROM DATOSPRODUCTOS WHERE PRODUCTO=?"
            el_producto = self.mi_conexion(query, (elemento,))
            el_producto = el_producto.fetchall()
            lista_items_precios.append(el_producto[0][2])

        numero_posicion = 0
        contador_numerico = 0

        ver_ticket = ''
        ver_ticket2 = ''

        try:
            while len(lista_items_precios) != contador_numerico:
                ver_ticket += f"{total_productos[numero_posicion]}\n"
                ver_ticket2 += f"{lista_items_precios[numero_posicion]}€\n"
                numero_posicion += 1
                contador_numerico += 1
        except:
            print('error')

        total_suma = 0
        for item in self.grid.get_children():
            celda = float(self.grid.set(item, 'col4'))
            total_suma += celda

        total_suma_print = [str('{0:.2f}'.format(total_suma))]

        self.cobrar_ticket(ver_ticket, ver_ticket2, total_suma_print[0])

    # -------------------------------------------------------------------------------- Cargar ticket en Treeview Cuenta

    def abrir_ticket_guardado(self, dato):
        valor = 1

        total_productos = []
        for item in self.grid.get_children():
            celda = self.grid.set(item, 'col1')
            total_productos.append(celda)

        if len(total_productos) == 0:

            try:
                with open(f'tickets/lista{dato}.pickle', 'rb') as archivo:
                    lista_productos = pickle.load(archivo)
                    for elemento in lista_productos:
                        query = f"SELECT * FROM DATOSPRODUCTOS WHERE PRODUCTO=?"
                        el_producto = self.mi_conexion(query, (elemento,))
                        el_producto = el_producto.fetchall()

                        try:

                            for productos in el_producto:
                                self.grid.insert('', 0, text=productos[0],
                                                 values=(productos[1], valor, productos[3], productos[2]))
                        except IndexError:
                            pass
            except:
                print('error al abrir archivo guardado')
        else:
            self.guardar_ticket()
            self.ventana_advertencia('El ticket actual\n se ha guardado')
            self.abrir_ticket_guardado(dato)

    # --------------------------------------------------------------------------- Devuelve acción al presionar un botón

    def efecto_boton(self, numero, lista_ordenada, num):

        if numero == lista_ordenada[0]:
            dato = lista_ordenada[0]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[1]:
            dato = lista_ordenada[1]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[2]:
            dato = lista_ordenada[2]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[3]:
            dato = lista_ordenada[3]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[4]:
            dato = lista_ordenada[4]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[5]:
            dato = lista_ordenada[5]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[6]:
            dato = lista_ordenada[6]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[7]:
            dato = lista_ordenada[7]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[8]:
            dato = lista_ordenada[8]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[9]:
            dato = lista_ordenada[9]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[10]:
            dato = lista_ordenada[10]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[11]:
            dato = lista_ordenada[11]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[12]:
            dato = lista_ordenada[12]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[13]:
            dato = lista_ordenada[13]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[14]:
            dato = lista_ordenada[14]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[15]:
            dato = lista_ordenada[15]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[16]:
            dato = lista_ordenada[16]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[17]:
            dato = lista_ordenada[17]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[18]:
            dato = lista_ordenada[18]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[19]:
            dato = lista_ordenada[19]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[20]:
            dato = lista_ordenada[20]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[21]:
            dato = lista_ordenada[21]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[22]:
            dato = lista_ordenada[22]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[23]:
            dato = lista_ordenada[23]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[24]:
            dato = lista_ordenada[24]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[25]:
            dato = lista_ordenada[25]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[26]:
            dato = lista_ordenada[26]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[27]:
            dato = lista_ordenada[27]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[28]:
            dato = lista_ordenada[28]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[29]:
            dato = lista_ordenada[29]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)
        elif numero == lista_ordenada[30]:
            dato = lista_ordenada[30]
            if num == 1:
                self.menu_productos(dato)
            elif num == 2:
                self.enviar_producto_cuenta(dato)
            elif num == 3:
                self.abrir_ticket_guardado(dato)


if __name__ == '__main__':
    raiz = Tk()
    raiz.title('TPV GRATIS')
    raiz.attributes('-alpha', 0.0)
    raiz.config(bg='grey')
    app = Aplicacion(raiz)
    app.mainloop()
