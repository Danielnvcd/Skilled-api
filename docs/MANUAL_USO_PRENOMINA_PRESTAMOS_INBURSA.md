# Manual de Uso: Prenomina, Prestamos y Ajuste Inbursa

## 1. Objetivo

Este manual explica como funciona en el sistema:

- la generacion de prenominas,
- la administracion de prestamos,
- el ajuste Inbursa,
- cuando se aplica cada descuento,
- y como debe reflejarse todo en la prenomina, el historico y los reportes.

Este documento describe el comportamiento actual del sistema segun la logica implementada.

## 2. Flujo General del Proceso

El flujo correcto es este:

1. Se capturan y cierran las horas de la semana.
2. Se genera la vista previa de prenomina desde el menu `Prenomina`.
3. Se guarda la prenomina semanal. En ese momento queda en estado `ABIERTA`.
4. Mientras esta `ABIERTA`, se pueden hacer ajustes:
   - descuentos manuales,
   - depositos extra,
   - ajuste de viaticos,
   - ajuste de festivos.
5. Cuando todo esta validado, se cierra la prenomina global.
6. Al cerrarla, la prenomina pasa a `APROBADO` y ahi se vuelven definitivos:
   - los descuentos de prestamos,
   - y los descuentos de Ajuste Inbursa de esa semana.

## 3. Modulo de Prenomina

### 3.1 Requisito para generar una prenomina

La prenomina solo se puede calcular para semanas cuyos reportes semanales ya estan cerrados, es decir, cuando el reporte se encuentra en estado:

- `TERMINADO`
- `PRENOMINA_CERRADA`

Si la semana no esta cerrada, el sistema no permite generar la nomina.

### 3.2 Como se genera

Ruta operativa:

1. Entrar al menu `Prenomina`.
2. Elegir la semana.
3. Abrir la vista de calculo.
4. Revisar la vista previa.
5. Guardar la prenomina.

Al guardarla:

- se crean los registros de prenomina por trabajador,
- quedan en estado `ABIERTA`,
- y los reportes de esa semana quedan marcados como `PRENOMINA_CERRADA`.

### 3.3 Como calcula el sistema la prenomina

Cada trabajador se calcula de forma global por semana.

#### Salario base

Depende del `tipo_nomina` del trabajador:

- `Por hora`: salario base = horas trabajadas x salario pactado semanal.
- `Cuadrado`: salario base = salario pactado semanal fijo.
- `Semanal`: salario base = salario pactado semanal fijo.

#### Horas extra

Solo para nomina `Semanal`:

- si el trabajador rebasa 50 horas productivas,
- las horas arriba de 50 se pagan con el valor de `hr_extra`.

Formula:

`pago_horas_extras = (horas_totales - 50) x hr_extra`

#### Viaticos

Se suman por registro diario cuando el dia tiene activado `aplica_viaticos`.

El monto sale de una de estas dos fuentes:

- si el registro diario tiene monto manual, se usa ese monto,
- si no, se usa el viatico configurado en el perfil del trabajador.

#### Festivos

Se pagan cuando el registro diario trae activado `aplica_dia_festivo`.

Formula:

`pago_festivos = numero_de_dias_festivos_marcados x pago_dia_festivo_del_trabajador`

#### Infonavit

Se toma directamente del campo financiero del trabajador:

`descuento_infonavit`

#### Ajuste Inbursa

Tiene dos formas de entrar a la prenomina:

1. Si existen descuentos del modulo `Ajuste` con fecha dentro de esa semana, se suman todos esos descuentos.
2. Si no existen descuentos semanales capturados, el sistema usa el valor fijo `ajuste_inbursa` del trabajador.

Esto significa que el modulo de ajustes tiene prioridad sobre el valor fijo del trabajador cuando hay descuentos registrados para la semana.

#### Prestamos

La prenomina suma automaticamente todos los descuentos programados de los prestamos activos del trabajador.

Formula:

`descuento_prestamos = suma de descuento_semanal de todos los prestamos activos`

#### Incidencias

Las incidencias si aparecen en la vista de edicion, pero el sistema no les descuenta dinero en automatico.

Eso significa que:

- una falta o retardo no baja solo el neto,
- el administrador debe agregar el descuento manualmente en la prenomina abierta.

#### Depositos extra

Los depositos extras se agregan manualmente desde la edicion de prenomina y aumentan el neto a pagar.

#### Descuentos manuales

Los descuentos manuales se agregan desde la edicion de prenomina y bajan el neto a pagar.

### 3.4 Formula final de la prenomina

#### Total percepciones

`salario_base + pago_horas_extras + pago_viaticos + pago_festivos + depositos_otros`

#### Total deducciones

`descuento_infonavit + ajuste_inbursa + descuento_incidencias + descuento_prestamos + descuentos_otros`

#### Neto a pagar

`total_percepciones - total_deducciones`

### 3.5 Que se puede editar mientras esta ABIERTA

Mientras la prenomina esta en estado `ABIERTA`, se puede:

- agregar descuentos manuales,
- eliminar descuentos manuales,
- agregar depositos extra,
- eliminar depositos extra,
- editar viaticos,
- editar festivos.

Cuando la prenomina ya esta `APROBADO`, ya no se puede editar.

### 3.6 Como cerrar correctamente una prenomina

Cuando la revision este completa:

1. Entrar a `Editar prenomina`.
2. Validar netos, descuentos, depositos, viaticos y festivos.
3. Presionar `Cerrar Nomina Global`.

Al cerrarla:

- todas las prenominas abiertas de esa semana pasan a `APROBADO`,
- los descuentos de Ajuste Inbursa de esa semana se marcan como `cobrado = True`,
- y los prestamos activos reciben su abono real en deuda.

## 4. Modulo de Prestamos

### 4.1 Para que sirve

Este modulo controla:

- el monto prestado,
- el descuento programado por periodo,
- el saldo pendiente,
- los abonos manuales,
- y la liquidacion total.

### 4.2 Como registrar un prestamo

En `Prestamos > Nuevo Prestamo` se captura:

- trabajador,
- monto total,
- plazo en semanas,
- descuento por periodo,
- frecuencia,
- fecha de inicio,
- motivo.

Al guardar:

- el prestamo queda en estado `ACTIVO`,
- `monto_restante` inicia igual al monto total,
- y el sistema recalcula las prenominas abiertas del trabajador.

### 4.3 Cuando se refleja el descuento del prestamo

El descuento del prestamo se refleja en la prenomina desde que el prestamo esta activo, porque la prenomina toma la suma de `descuento_semanal` de los prestamos activos.

Pero hay una diferencia importante:

- en la prenomina abierta solo se refleja como descuento calculado,
- la deuda del prestamo no baja realmente hasta cerrar la prenomina.

### 4.4 Cuando baja realmente la deuda

La deuda del prestamo se descuenta de verdad al cerrar la prenomina semanal.

En ese momento el sistema:

1. revisa todos los prestamos activos del trabajador,
2. toma el descuento programado de cada uno,
3. aplica como abono real el menor entre:
   - el descuento programado,
   - y el saldo restante.

Despues:

- se registra un `AbonoPrestamo` de tipo `NOMINA`,
- baja el `monto_restante`,
- y si llega a cero, el prestamo pasa a `LIQUIDADO`.

### 4.5 Abonos manuales

Si se registra un abono extraordinario manual:

- se descuenta inmediatamente del saldo del prestamo,
- se guarda como `AbonoPrestamo` tipo `MANUAL`,
- y se recalculan las prenominas abiertas del trabajador.

### 4.6 Liquidacion manual

Si se usa la opcion `Liquidar`:

- el sistema genera un abono manual por el saldo restante,
- deja el saldo en cero,
- y cambia el prestamo a `LIQUIDADO`.

### 4.7 Como debe reflejarse un prestamo en el sistema

Debe verse en cuatro lugares:

1. En `Prestamos`, como saldo activo o liquidado.
2. En la edicion de prenomina, dentro de `Prestamos Activos`.
3. En la prenomina semanal, como `descuento_prestamos`.
4. En el historial del prestamo, como abonos `NOMINA` o `MANUAL`.

### 4.8 Aclaracion importante sobre el deposito del prestamo

Actualmente el modulo de prestamos controla el descuento y la deuda, pero no genera automaticamente un deposito en prenomina por el monto prestado.

Es decir:

- crear un prestamo no aumenta por si solo el neto de una prenomina,
- ni llena automaticamente `depositos_prestamos`.

Si la empresa necesita reflejar el dinero entregado al trabajador dentro de la semana, hoy debe registrarse aparte, por ejemplo como deposito extra en la prenomina correspondiente.

## 5. Modulo de Ajuste Inbursa

### 5.1 Para que sirve

Este modulo sirve para recuperar descuentos relacionados con depositos adelantados de Inbursa dentro de un periodo de ajuste.

El sistema maneja:

- un periodo,
- los trabajadores incluidos en ese periodo,
- la meta por trabajador,
- y los descuentos capturados por fecha.

### 5.2 Como crear un periodo

En `Ajuste` se crea un periodo con:

- nombre,
- fecha inicio,
- fecha fin,
- trabajadores,
- y monto meta por trabajador.

Reglas:

- la fecha inicial debe ser menor que la final,
- no puede existir otro periodo con fechas traslapadas,
- y se debe seleccionar al menos un trabajador.

### 5.3 Como capturar descuentos

Dentro del detalle del periodo:

1. Elegir al trabajador.
2. Capturar el monto.
3. Elegir la fecha exacta del descuento.
4. Agregar notas si hace falta.

Cada registro crea un `AjusteDescuento`.

### 5.4 Cuando entra un ajuste Inbursa a la prenomina

El descuento entra a la prenomina cuando la `fecha_descuento` cae dentro de la semana de prenomina del trabajador.

El sistema suma todos los descuentos de esa semana y los muestra en el campo:

`ajuste_inbursa`

### 5.5 Cuando se considera realmente cobrado

Aunque el descuento ya aparezca en la prenomina abierta o en la vista previa, el ajuste se considera cobrado hasta cerrar la prenomina.

En ese momento:

- los registros del ajuste de esa semana se marcan como cobrados,
- y ya no se pueden eliminar desde el periodo si quedaron ligados a una prenomina aprobada.

### 5.6 Como debe reflejarse el ajuste Inbursa

Debe verse en estos puntos:

1. En el detalle del periodo de ajuste:
   - meta,
   - descontado,
   - restante,
   - progreso,
   - detalle por fecha.
2. En la prenomina semanal como `Ajuste Inbursa`.
3. En el recibo PDF y reportes Excel de prenomina.
4. En el historico cuando la prenomina ya fue aprobada.

## 6. Como Debe Reflejarse Todo

### 6.1 En la prenomina abierta

Debe reflejarse inmediatamente:

- prestamos activos como descuento programado,
- descuentos manuales,
- depositos extra,
- viaticos editados,
- festivos editados,
- ajuste Inbursa de la semana,
- y el neto recalculado.

### 6.2 Al aprobar la prenomina

Deben ocurrir tres cosas:

1. La semana queda bloqueada para edicion.
2. Los descuentos Inbursa de esa semana quedan marcados como cobrados.
3. Los prestamos registran su abono real y actualizan su saldo restante.

### 6.3 En el historico

Solo deben aparecer prenominas en estado `APROBADO`.

El historico debe mostrar ya el resultado final pagado, no la vista editable.

### 6.4 En reportes

#### Excel de prenomina

Debe incluir, entre otros:

- salario base,
- horas extras,
- viaticos,
- festivos,
- otros depositos,
- ajuste Inbursa,
- otros descuentos,
- abono prestamos,
- descuento incidencias,
- total deducciones,
- total a pagar.

#### Excel de prestamos

Debe incluir:

- monto original,
- total abonado,
- saldo restante,
- descuento semanal,
- estado,
- motivo.

#### Excel de ajustes

Debe incluir:

- resumen por trabajador,
- meta,
- total descontado,
- saldo restante,
- y detalle por fecha de descuento.

## 7. Recomendaciones Operativas

Para evitar diferencias en pago, usar este orden:

1. Cerrar primero las horas de la semana.
2. Crear o actualizar prestamos antes de cerrar prenomina.
3. Capturar descuentos Inbursa con su fecha real dentro del periodo.
4. Guardar prenomina y revisarla en estado `ABIERTA`.
5. Aplicar manualmente descuentos por incidencias cuando corresponda.
6. Agregar depositos extras solo si deben aumentar el neto real a pagar.
7. Aprobar la prenomina solo cuando todo este validado.

## 8. Observaciones del Comportamiento Actual

Actualmente existen campos en el modelo que aparecen en reportes, pero no forman parte del flujo principal visible de captura de esta version:

- `depositos_prestamos`
- `recuperacion_manual`
- `descuento_incidencias` como descuento automatico

En la practica actual:

- `depositos_prestamos` no se llena automaticamente al crear prestamos,
- `recuperacion_manual` existe en el modelo y en algunas salidas, pero no tiene captura operativa en este flujo,
- y las incidencias se muestran para decision administrativa, no como descuento automatico.

## 9. Resumen Ejecutivo

La regla mas importante del sistema es esta:

- `Prestamo activo` se ve en prenomina desde antes, pero baja la deuda hasta aprobar la semana.
- `Ajuste Inbursa` se ve en prenomina segun su fecha de descuento, pero se considera cobrado hasta aprobar la semana.
- `Incidencias` se muestran, pero el descuento lo decide y captura el administrador.
- `Depositos extra` y ajustes manuales recalculan el neto mientras la prenomina siga abierta.

