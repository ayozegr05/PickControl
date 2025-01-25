import express from 'express';
import Book from './model.js';
import Pick from './model.js';
// import Apuesta from './model.js';

const router = express.Router();

router.get("/apuestas", async (req, res) => {
    try {
        // Usamos el modelo Apuesta para obtener todas las apuestas desde la colección 'Picks'
        const picks = await Pick.find();  // Devuelve todas las apuestas

        // Si encontramos apuestas, las devolvemos como respuesta en formato JSON
        res.status(200).json({ picks });
    } catch (error) {
        // En caso de error, respondemos con un mensaje de error
        console.error("Error al obtener apuestas:", error);
        res.status(400).json({ error: "Error al obtener las apuestas" });
    }
});

router.post("/apuestas", async (req, res) => {
    const { Apuesta, Informante, TipoDeApuesta, Casa, Acierto, CantidadApostada, Cuota } = req.body;

    try {
        // Crear una nueva entrada en la colección 'Picks'
        const nuevaApuesta = new Pick({
            Apuesta,
            Informante,
            TipoDeApuesta,
            Casa,
            Acierto,  // Puede ser nulo o vacío en el momento de la creación
            CantidadApostada,
            Cuota
        });

        // Guardar la nueva apuesta en la base de datos
        await nuevaApuesta.save();

        res.status(201).json({ message: "Apuesta creada exitosamente", nuevaApuesta });
    } catch (error) {
        console.error("Error al crear la apuesta:", error);
        res.status(400).json({ error: "Error al crear la apuesta" });
    }
});

// Endpoint PUT para actualizar el campo Acierto
router.put("/apuesta/:id", async (req, res) => {
    const { id } = req.params;
    const { Acierto } = req.body;
  
    console.log("ID recibido:", id);
    console.log("Acierto recibido:", Acierto);
  
    try {
      const apuestaActualizada = await Pick.findByIdAndUpdate(
        id,
        { Acierto },
        { new: true } // Esto devolverá el documento actualizado
      );
  
      if (!apuestaActualizada) {
        console.error("Apuesta no encontrada para ID:", id);
        return res.status(404).json({ error: "Apuesta no encontrada" });
      }
  
      res.status(200).json({ message: "Apuesta actualizada", apuestaActualizada });
    } catch (error) {
      console.error("Error al actualizar la apuesta:", error);
      res.status(400).json({ error: "Error al actualizar la apuesta" });
    }
  });
  

// Endpoint para obtener apuestas de un informante específico
router.get("/informante/:informante", async (req, res) => {
    const { informante } = req.params;  // Extraemos el nombre del informante desde los parámetros de la URL

    try {
        // Obtener todas las apuestas del informante
        const apuestas = await Pick.find({ Informante: informante });

        if (apuestas.length === 0) {
            return res.status(404).json({ message: "No se encontraron apuestas para este informante." });
        }

        // Calcular las estadísticas
        let totalApuestas = apuestas.length;  // Número total de apuestas
        let totalAciertos = 0;  // Contador de aciertos
        let ganancias = 0;  // Variable para calcular las ganancias totales
        let apuestasConAcierto = 0;  // Contador de apuestas que tienen un resultado de "True" o "False"

        // Recorremos todas las apuestas para realizar los cálculos
        apuestas.forEach((apuesta) => {
            // Calculamos la ganancia de la apuesta
            const ganancia = apuesta.CantidadApostada * apuesta.Cuota;

            // Agregamos la ganancia a la propiedad de la apuesta
            apuesta.ganancia = ganancia.toFixed(2);  // Guardamos la ganancia calculada en cada apuesta

            if (apuesta.Acierto === 'True' || apuesta.Acierto === 'False') {
                apuestasConAcierto++;  // Solo consideramos las apuestas con un acierto "True" o "False"
                if (apuesta.Acierto === 'True') {
                    totalAciertos++;  // Si el acierto es "True", incrementamos el contador de aciertos
                    ganancias += ganancia;  // Sumamos la ganancia de la apuesta a las ganancias totales
                }
            }
        });

        // Calculamos el porcentaje de aciertos, evitando división por 0
        const porcentajeAciertos = apuestasConAcierto > 0 ? (totalAciertos / apuestasConAcierto) * 100 : 0;

        // Devolvemos los datos de las apuestas y las estadísticas en la respuesta
        return res.status(200).json({
            apuestas,               // Devolvemos todas las apuestas (con sus ganancias individuales)
            totalApuestas,          // Total de apuestas realizadas por el informante
            totalAciertos,          // Total de aciertos ("True")
            ganancias,              // Ganancias totales
            porcentajeAciertos      // Porcentaje de aciertos
        });

    } catch (error) {
        console.error("Error al obtener datos del informante:", error);
        return res.status(500).json({ error: "Error al obtener las apuestas." });
    }
});



router.delete("/apuestas/:id", async (req, res) => {
    const { id } = req.params;

    try {
        // Buscar y eliminar la apuesta por su ID
        const apuestaEliminada = await Pick.findByIdAndDelete(id);

        if (!apuestaEliminada) {
            return res.status(404).json({ error: "Apuesta no encontrada" });
        }

        res.status(200).json({ message: "Apuesta eliminada exitosamente", apuestaEliminada });
    } catch (error) {
        console.error("Error al eliminar la apuesta:", error);
        res.status(400).json({ error: "Error al eliminar la apuesta" });
    }
});




export default router;
