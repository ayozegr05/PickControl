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

    try {
        // Buscar la apuesta por su ID y actualizar el campo 'Acierto'
        const apuestaActualizada = await Pick.findByIdAndUpdate(
            id,
            { Acierto },
            { new: true }  // Esto devolverá el documento actualizado
        );

        if (!apuestaActualizada) {
            return res.status(404).json({ error: "Apuesta no encontrada" });
        }

        res.status(200).json({ message: "Apuesta actualizada", apuestaActualizada });
    } catch (error) {
        console.error("Error al actualizar la apuesta:", error);
        res.status(400).json({ error: "Error al actualizar la apuesta" });
    }
});
router.get("/informante/:informante", async (req, res) => {
    const { informante } = req.params;

    try {
        // Buscar todas las apuestas de ese informante
        const apuestas = await Pick.find({ Informante: informante });

        let totalApuestas = apuestas.length;  // Todas las apuestas, sin importar si tienen Acierto o no
        let totalAciertos = 0;
        let ganancias = 0;
        let apuestasConAcierto = 0;  // Contador para las apuestas con Acierto definido

        apuestas.forEach(apuesta => {
            // Contamos las apuestas con Acierto definido
            if (apuesta.Acierto === 'True' || apuesta.Acierto === 'False') {
                apuestasConAcierto++;

                // Si Acierto es True, sumar a los aciertos y calcular ganancias
                if (apuesta.Acierto === 'True') {
                    totalAciertos += 1;
                    ganancias += apuesta.CantidadApostada * apuesta.Cuota;  // Ganancia ajustada por la cuota
                }
            }
        });

        // El porcentaje de aciertos solo se calcula tomando las apuestas con Acierto definido
        const porcentajeAciertos = apuestasConAcierto > 0 ? (totalAciertos / apuestasConAcierto) * 100 : 0;

        res.status(200).json({
            totalApuestas,
            totalAciertos,
            ganancias,
            porcentajeAciertos
        });
    } catch (error) {
        console.error("Error al obtener datos del informante:", error);
        res.status(400).json({ error: "Error al obtener datos" });
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
