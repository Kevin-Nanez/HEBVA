---
codigo: "FAQ-OPS-001"
titulo: "Preguntas frecuentes para gerentes de tienda"
version: "1.4"
vigencia: "2026-01-12"
area: "Dirección de Operaciones de Tienda"
archivo: "faq_gerentes_de_tienda.pdf"
alcance: "No declara alcance; ante una diferencia con un documento normativo prevalece la política o el procedimiento (§1)"
revision: "2026-10-31: cada tabla (P20 (p. 5) y §7 (p. 7)) cotejada celda por celda contra la página renderizada del PDF y contra pdftotext -raw; títulos y páginas cotejados contra los renglones en negrita del PDF. Única corrección: encabezados partidos a media palabra (CORRECCIONES_DE_ENCABEZADO en agente/extraer_politicas.py)."
---
<!-- pagina 1 -->
## 1. Presentación {p=1}
Este documento reúne las preguntas que con mayor frecuencia plantean los Gerentes y Subgerentes de Tienda a la Gerencia Regional de Operaciones, a la Mesa de Servicio de TI y a las áreas corporativas de apoyo. Las respuestas son orientaciones prácticas y resumidas; en caso de duda o de diferencia con un documento normativo, prevalece siempre la política o el procedimiento vigente al que se hace referencia. Las preguntas se agrupan en cinco bloques: operación diaria, mermas y perecederos, servicio al cliente, tecnología y mantenimiento, y personal.
## 2. Operación diaria {p=1}
### P1. ¿A qué hora debo llegar a la tienda para la apertura? {p=1}
El Gerente en turno debe estar en la tienda noventa minutos antes de la hora de apertura al público, es decir, a las 5:30 horas en una tienda con horario estándar de 7:00 a 22:00. En ese lapso se desactiva la alarma, se realiza el recorrido de seguridad, se verifican temperaturas, se arma el fondo fijo y se realiza la reunión de arranque. El detalle de cada actividad, su hora y su responsable está en el checklist de apertura del Manual de apertura y cierre de tienda (MAN-OPS-007).
### P2. ¿Cuál es el horario de recepción de proveedores? {p=1}
La recepción de mercancía en andén se realiza de lunes a sábado de 6:00 a 13:00 horas. Fuera de ese horario únicamente se recibe a proveedores con cita previamente autorizada por el Jefe de Recepción, por ejemplo entregas nocturnas de pan o de lácteos en tiendas de alto volumen. Los domingos no se recibe mercancía, salvo autorización expresa de la Gerencia Regional en temporadas altas. Todo proveedor que llegue sin cita fuera de horario debe reprogramarse; no se le recibe "por esta vez".
<!-- pagina 2 -->
### P3. ¿Cómo hago los cortes de caja y qué diferencias son aceptables? {p=2}
Cada cajero realiza su corte al terminar el turno desde la opción "Corte de cajero" de la terminal, y cuenta el efectivo en el área de conteo en presencia del Jefe de Cajas. Las diferencias hasta 20 pesos se consideran tolerables; las de 20.01 a 200 pesos son menores y se comentan con el cajero; las de 200.01 a 1,000 pesos son mayores y requieren acta administrativa; y las superiores a 1,000 pesos son críticas y se reportan de inmediato a Prevención de Pérdidas. Al final del día el Gerente en turno concilia el arqueo consolidado contra el reporte de ventas y firma la bitácora.
### P4. ¿Puedo usar el fondo fijo para pagar un gasto urgente de la tienda? {p=2}
No. El fondo fijo se destina exclusivamente a los cajones de cambio y a la reserva de morralla de la caja fuerte. Los gastos menores urgentes (por ejemplo, un plomero de emergencia o material de limpieza) se cubren con la caja chica administrativa, que es independiente y tiene su propio procedimiento de comprobación. Usar el fondo fijo para gastos, préstamos o cambio a terceros es una falta al Reglamento Interior de Trabajo.
### P5. ¿Qué debo registrar en la bitácora todos los días? {p=2}
Como mínimo: horas de desactivación y activación de la alarma, quiénes abrieron y cerraron, resultado del arqueo consolidado, lecturas de temperatura de apertura y cierre, incidencias operativas y de seguridad con hora y acción tomada, folios de tickets levantados a Mantenimiento y a Mesa de Servicio, visitas de autoridades o auditoría y observaciones generales. La bitácora se lleva en la aplicación "Tienda al Día" y se firma diariamente.
### P6. ¿Cómo pido reposición urgente de un producto que se está agotando? {p=2}
Primero verifica en SIGMA que el inventario teórico coincida con el físico; con frecuencia el sistema no genera pedido porque cree que hay existencia. Si el inventario está correcto y el producto de alta rotación se agotará antes del siguiente pedido programado, registra una solicitud de reabasto extraordinario en SIGMA, módulo "Pedido urgente", indicando el código del producto, la cantidad y el motivo (promoción, evento local, error de pedido). Las solicitudes capturadas antes de las 11:00 horas se surten desde el centro de distribución regional al día siguiente hábil, sujeto a disponibilidad. Para productos de entrega directa del proveedor, contacta al Jefe de Recepción para que gestione la cita extraordinaria. Si el faltante es de un producto de folleto, notifica además a la Gerencia Regional para que evalúe una comunicación al cliente en tienda.
### P7. ¿Qué hago si hay una remodelación u obra en mi tienda? {p=2}
Revisa el plan de afectación que la Gerencia de Proyectos debe enviarte con al menos quince días de anticipación. Calcula qué porcentaje del área de venta quedará inhabilitado: si la obra reduce el área de venta en más del 30 %, la tienda opera con horario reducido autorizado por la Gerencia Regional, colocas aviso al público con cuarenta y ocho horas de anticipación y levantas un ticket a Mantenimiento con prioridad Alta indicando fechas, duración y áreas afectadas. Si la reducción es del 30 % o menos, mantienes el horario habitual y solo registras la obra en la bitácora. En cualquier caso, exige mamparas rígidas de piso a techo, programa los trabajos ruidosos fuera del horario de atención y no exhibas alimentos a menos de tres metros de la zona de obra.
<!-- pagina 3 -->
### P8. ¿Puedo cambiar el horario de la tienda por baja afluencia? {p=3}
No de manera unilateral. Cualquier cambio de horario, temporal o permanente, requiere autorización de la Gerencia Regional y debe comunicarse al público con al menos cuarenta y ocho horas de anticipación. Las únicas excepciones son las condiciones de seguridad que impiden abrir (falta de agua, falla total de energía sin generador, intrusión, fuga de gas o inundación), en las que debes notificar de inmediato a la Gerencia Regional y colocar aviso en la puerta.
## 3. Mermas y perecederos {p=3}
### P9. ¿Cuánta merma es aceptable en mi tienda? {p=3}
No existe una cifra única para toda la tienda: la merma aceptable depende de la categoría de producto y se mide cada mes como porcentaje de la venta neta de esa categoría. Los valores oficiales, junto con los umbrales de alerta y la frecuencia mínima de conteo, se encuentran en la tabla de tolerancias de merma por categoría de la Política de mermas y caducidad (POL-OPS-014). Consulta siempre esa tabla en la versión vigente del documento y no cifras de memoria ni de versiones anteriores; si tu tienda supera el umbral de alerta en alguna categoría, tienes cinco días hábiles para presentar un plan de acción a la Gerencia Regional.
### P10. ¿Cuándo debo retirar del anaquel un producto próximo a caducar? {p=3}
La regla general para perecederos es retirar el producto un día antes de la fecha de caducidad impresa. Para lácteos la regla es más estricta: se retiran dos días antes. Antes de llegar a ese punto, el producto identificado como próximo a vencer debe marcarse con etiqueta naranja y ubicarse en el módulo de "Última oportunidad" con la rebaja escalonada que corresponda a los días restantes, conforme a la Política de mermas y caducidad (POL-OPS-014). Todo producto retirado se registra en SIGMA con la causa "Caducidad" el mismo día.
### P11. ¿Qué hago si se va la luz y los refrigeradores dejan de funcionar? {p=3}
Confirma que la planta de emergencia haya arrancado; si no lo hace, Mantenimiento de tienda intenta el arranque manual y, si falla, levantas ticket con prioridad Crítica. Coloca de inmediato las cortinas nocturnas en los muebles refrigerados abiertos y mantén cerradas las puertas de las cámaras. Las cámaras conservan la temperatura segura durante cuatro horas con las puertas cerradas; los muebles abiertos, solo dos horas. Registra temperaturas cada treinta minutos. Si la falla dura entre dos y cuatro horas, traslada el producto de los muebles abiertos a las cámaras. Si supera cuatro horas, o el producto refrigerado pasó de 7 °C por más de dos horas, retira el producto de la venta y regístralo en SIGMA como "Falla de cadena de frío" con el formato de temperaturas como evidencia. El protocolo completo está en el Manual de apertura y cierre de tienda (MAN-OPS-007).
### P12. ¿Puedo regalar al personal el producto que ya se registró como merma? {p=3}
No. Una vez registrado en SIGMA, el producto mermado debe destruirse o desecharse conforme a la política; está prohibido que el personal lo consuma, se lo lleve, lo regale o lo venda, aun cuando el registro ya esté autorizado. El producto apto para consumo pero no para venta solo puede canalizarse al programa de donación institucional mediante el convenio corporativo vigente y registrándolo con la causa "Donación".
<!-- pagina 4 -->
### P13. ¿Qué pasa si detecto que un producto se vendió ya caducado? {p=4}
Retira de inmediato todas las unidades del mismo lote del piso de venta, registra la merma en SIGMA con causa "Caducidad" y notifica el mismo día a la Gerencia Regional y al área de Aseguramiento de Calidad. Si el cliente afectado acude a la tienda, la devolución procede sin restricciones conforme al Procedimiento de devoluciones y cambios (PRO-SAC-021). La venta de producto caducado es un hallazgo crítico de auditoría, por lo que documenta en la bitácora qué falló en la revisión de fechas y qué acción correctiva tomaste.
### P14. ¿Con qué frecuencia debo contar el inventario de cada departamento? {p=4}
La frecuencia mínima de conteo por categoría está definida en la misma tabla de tolerancias de la Política de mermas y caducidad (POL-OPS-014) y va desde diaria en categorías de alta merma hasta trimestral en no comestibles. Los conteos se hacen preferentemente antes de abrir o después de cerrar, con al menos dos personas, una de ellas de un departamento distinto al que se cuenta. Adicionalmente participas en el inventario general semestral que coordina Finanzas.
## 4. Servicio al cliente {p=4}
### P15. Un cliente quiere devolver una licuadora que compró hace dos semanas. ¿Procede? {p=4}
Sí, siempre que presente el ticket de compra y el producto con su empaque original completo, accesorios y manuales. Para electrodomésticos y electrónicos menores como licuadoras, tostadores, planchas o cafeteras, el cliente tiene 15 días con ticket para solicitar la devolución o el cambio en tienda; después de ese plazo se le orienta para ejercer la garantía del fabricante en el centro de servicio autorizado. Si el aparato presenta falla de funcionamiento dentro del plazo, la tienda realiza el cambio directamente sin remitir al cliente al fabricante. Si el cliente no trae el empaque original, la devolución requiere autorización del Gerente de Tienda.
### P16. ¿Puedo aceptar una devolución sin ticket? {p=4}
Sí, con restricciones. Solo procede para producto no perecedero y que no sea medicamento, cerrado, sin uso y del surtido activo de la tienda. El cliente presenta identificación oficial y el reembolso se hace exclusivamente en vale de tienda por el precio más bajo de los últimos treinta días. Un mismo cliente puede hacer como máximo tres devoluciones sin ticket en doce meses por un acumulado de tres mil pesos; el sistema bloquea la operación al exceder los límites y solo tú puedes liberar el bloqueo con justificación. Consulta el Procedimiento de devoluciones y cambios (PRO-SAC-021).
### P17. ¿Qué montos de devolución puedo autorizar yo y cuáles debo escalar? {p=4}
El Ejecutivo de Servicio al Cliente autoriza hasta 500 pesos; el Supervisor hasta 3,000; el Gerente de Servicio al Cliente o Subgerente hasta 10,000; el Gerente de Tienda hasta 30,000. Por encima de 30,000 pesos se requiere además el visto bueno por correo del Gerente Regional de Servicio al Cliente antes de ejecutar la operación. Independientemente del monto, siempre requieren tu autorización las devoluciones fuera de plazo por defecto de fabricación, la liberación de bloqueos por límites sin ticket, los casos con señales de fraude y los electrodomésticos sin empaque original.
<!-- pagina 5 -->
### P18. ¿Cómo se atienden los pedidos en línea (ecommerce) que se recogen o devuelven en tienda? {p=5}
Los pedidos en línea con entrega en tienda se preparan en el área de "Recoge en tienda" y se entregan al cliente contra el código de recolección de la aplicación y una identificación. Si el cliente desea devolver un artículo de un pedido en línea, se localiza la transacción por número de pedido y se aplica el Procedimiento de devoluciones y cambios (PRO-SAC-021) con los mismos plazos que en tienda; el reembolso se hace al medio de pago del pedido y lo gestiona el área de Ecommerce en un plazo de cinco a diez días hábiles. Si el pedido llegó incompleto o con un producto equivocado, el error es atribuible a la empresa y la devolución procede sin restricciones de empaque dentro de los noventa días siguientes. Las incidencias recurrentes se reportan al Procedimiento de atención a pedidos en línea (PRO-ECO-009).
### P19. ¿Qué hago si sospecho que una devolución es fraudulenta? {p=5}
Solicita la presencia del Gerente de Servicio al Cliente o atiende personalmente el caso. Revisa las señales de alerta: ticket alterado o inexistente en sistema, empaque resellado, etiqueta de otra cadena, número de serie distinto al vendido o patrones de devoluciones frecuentes. Si decides que no procede, explícalo con amabilidad, no retengas el producto ni el ticket y no confrontes al cliente. Documenta el caso en la bitácora de incidencias del módulo y, si hay indicios de delito, notifica a Prevención de Pérdidas conforme al protocolo corporativo.
## 5. Tecnología y mantenimiento {p=5}
### P20. ¿Cómo levanto un ticket a la Mesa de Servicio y qué prioridades existen? {p=5}
Entra al portal interno de la Mesa de Servicio, sección "Reportar incidencia", o llama a la extensión corporativa de soporte si la tienda no tiene acceso al portal. Indica número de tienda, equipo afectado, descripción breve, las acciones de primer nivel que ya realizaste y tu nombre y cargo; anota el folio en la bitácora. La prioridad la asignas al reportar y la Mesa de Servicio puede ajustarla. Los tiempos comprometidos son los siguientes:

| Prioridad | Cuándo aplica | Tiempo de primera respuesta | Tiempo objetivo de solución |
|---|---|---|---|
| Baja | Incidencia que no afecta la venta ni la operación (un handheld de varios, una impresora de oficina). | 8 horas hábiles | 5 días hábiles |
| Media | Afecta parcialmente la operación pero existe alternativa (una caja sin impresora, una báscula de varias). | 4 horas hábiles | 2 días hábiles |
| Alta | Afecta de forma importante la venta o la seguridad (varias cajas fuera, terminal bancaria general, remodelación mayor). | 1 hora | 8 horas |
| Crítica | Detiene la operación de la tienda o pone en riesgo el producto o a las personas (servidor caído, sin enlace de datos, planta de emergencia que no arranca, cámara de refrigeración fuera). | 15 minutos | 4 horas |

- Prioridad «Baja», Cuándo aplica = Incidencia que no afecta la venta ni la operación (un handheld de varios, una impresora de oficina).
- Prioridad «Baja», Tiempo de primera respuesta = 8 horas hábiles
- Prioridad «Baja», Tiempo objetivo de solución = 5 días hábiles
- Prioridad «Media», Cuándo aplica = Afecta parcialmente la operación pero existe alternativa (una caja sin impresora, una báscula de varias).
- Prioridad «Media», Tiempo de primera respuesta = 4 horas hábiles
- Prioridad «Media», Tiempo objetivo de solución = 2 días hábiles
- Prioridad «Alta», Cuándo aplica = Afecta de forma importante la venta o la seguridad (varias cajas fuera, terminal bancaria general, remodelación mayor).
- Prioridad «Alta», Tiempo de primera respuesta = 1 hora
- Prioridad «Alta», Tiempo objetivo de solución = 8 horas
- Prioridad «Crítica», Cuándo aplica = Detiene la operación de la tienda o pone en riesgo el producto o a las personas (servidor caído, sin enlace de datos, planta de emergencia que no arranca, cámara de refrigeración fuera).
- Prioridad «Crítica», Tiempo de primera respuesta = 15 minutos
- Prioridad «Crítica», Tiempo objetivo de solución = 4 horas

<!-- pagina 6 -->
### P21. La impresora de una caja no imprime. ¿Qué hago antes de reportar? {p=6}
Pide al Jefe de Cajas que verifique que hay papel y que el rollo esté con la cara térmica hacia arriba; luego apague la impresora, espere diez segundos y la encienda de nuevo. Si persiste, reinicie la terminal desde el menú del sistema (nunca desconectándola) y haga una impresión de prueba desde "Diagnóstico". Si la falla continúa después del reinicio, levanta un ticket a la Mesa de Servicio con prioridad Media indicando número de caja y modelo de impresora, y reasigna al cajero a otra caja. Una caja sin impresora no se abre al público salvo que el ticket digital esté habilitado y el cliente lo acepte.
### P22. ¿Qué hago si la tienda pierde por completo el enlace de datos? {p=6}
Activa el modo de operación sin conexión en las cajas para seguir vendiendo y llama por teléfono a la Mesa de Servicio para levantar el ticket con prioridad Crítica, ya que el portal no estará disponible. No manipules el servidor de tienda ni el equipo de comunicaciones. Las ventas capturadas sin conexión se sincronizan automáticamente al restablecerse el enlace; verifica al cierre que el reporte de ventas del sistema coincida con el arqueo.
### P23. ¿Cada cuánto debo revisar las temperaturas de refrigeradores y cámaras? {p=6}
En operación normal se registran al menos dos veces al día, en la apertura y en el cierre, y quedan en la bitácora. Durante una contingencia de energía o de equipo, cada treinta minutos en el formato de contingencia. Si al restablecerse la energía una cámara no recupera su temperatura objetivo en sesenta minutos, levanta ticket a Mantenimiento.
## 6. Personal {p=6}
### P24. Un proveedor o un cliente me pide el teléfono o el domicilio de un colaborador. ¿Puedo darlo? {p=6}
No. La información de contacto y los datos personales de los empleados (teléfono, domicilio, correo personal, número de seguridad social, datos familiares, horarios) son confidenciales y están protegidos por la legislación de protección de datos personales y por la política corporativa de privacidad. No se comparten por canales no oficiales, ni verbalmente, ni por mensajería instantánea, ni a proveedores, clientes, autoridades sin requerimiento formal o compañeros de otra área. Cualquier solicitud de datos de un colaborador se canaliza a Recursos Humanos, que es el único área autorizada para atenderla y verificar su legitimidad. Si alguien insiste, toma sus datos y remítelo al buzón oficial de RH.
### P25. ¿Qué hago si un colaborador no se presenta a su turno y no hay quien cubra la caja? {p=6}
Reasigna al personal disponible priorizando cajas y perecederos; puedes convocar personal de descanso con pago de tiempo extra conforme al Reglamento Interior de Trabajo y registrar el cambio de horario en el sistema de asistencia. Documenta la ausencia en la bitácora y notifícala a Recursos Humanos ese mismo día para el seguimiento correspondiente. Si la falta de personal impide operar con seguridad (por ejemplo, una sola persona en cajas en horario pico), informa a la Gerencia Regional para evaluar apoyo de tiendas cercanas.
<!-- pagina 7 -->
## 7. Control de cambios {p=7}

| Versión | Fecha | Descripción del cambio | Autorizó |
|---|---|---|---|
| 1.0 | 1 de agosto de 2023 | Emisión inicial con 15 preguntas. | Dirección de Operaciones de Tienda |
| 1.1 | 15 de febrero de 2024 | Se agregan preguntas sobre pedidos en línea y devoluciones sin ticket. | Dirección de Operaciones de Tienda |
| 1.2 | 1 de agosto de 2024 | Se agrega la tabla de prioridades de la Mesa de Servicio. | Dirección de Operaciones de Tienda |
| 1.3 | 1 de marzo de 2025 | Se actualizan referencias a la Política de mermas y caducidad versión 3.2. | Dirección de Operaciones de Tienda |
| 1.4 | 12 de enero de 2026 | Se agregan preguntas sobre remodelaciones, datos personales de colaboradores y ausencias; total de 25 preguntas. | Dirección de Operaciones de Tienda |

- Versión «1.0», Fecha = 1 de agosto de 2023
- Versión «1.0», Descripción del cambio = Emisión inicial con 15 preguntas.
- Versión «1.0», Autorizó = Dirección de Operaciones de Tienda
- Versión «1.1», Fecha = 15 de febrero de 2024
- Versión «1.1», Descripción del cambio = Se agregan preguntas sobre pedidos en línea y devoluciones sin ticket.
- Versión «1.1», Autorizó = Dirección de Operaciones de Tienda
- Versión «1.2», Fecha = 1 de agosto de 2024
- Versión «1.2», Descripción del cambio = Se agrega la tabla de prioridades de la Mesa de Servicio.
- Versión «1.2», Autorizó = Dirección de Operaciones de Tienda
- Versión «1.3», Fecha = 1 de marzo de 2025
- Versión «1.3», Descripción del cambio = Se actualizan referencias a la Política de mermas y caducidad versión 3.2.
- Versión «1.3», Autorizó = Dirección de Operaciones de Tienda
- Versión «1.4», Fecha = 12 de enero de 2026
- Versión «1.4», Descripción del cambio = Se agregan preguntas sobre remodelaciones, datos personales de colaboradores y ausencias; total de 25 preguntas.
- Versión «1.4», Autorizó = Dirección de Operaciones de Tienda
