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


router.get('/book/:id', async (req, res) => {
    try {
        const book = await Book.findById(req.params.id); // Busca un libro por su ID en la base de datos
        if (!book) {
            return res.status(404).json({ message: 'Libro no encontrado' });
        }
        res.json(book); // Devuelve el libro encontrado en formato JSON
    } catch (error) {
        console.error(error);
        res.status(500).json({ message: 'Error interno del servidor' });
    }
});

router.post('/new-book', async (req, res) => {
    try {
        const { title, author, genre, year, photo } = req.body;

        // Crear una nueva instancia del modelo Book
        const newBook = new Book({
            title,
            author,
            genre,
            year,
            photo
        });

        // Guardar el libro en la base de datos
        const savedBook = await newBook.save();

        res.status(201).json(savedBook);
    } catch (error) {
        console.error('Error adding new book:', error);
        res.status(500).json({ message: 'Internal server error' });
    }
});

router.put('/update-book/:id', async (req, res) => {
    const { id } = req.params;

    try {
        const updatedBook = await Book.findByIdAndUpdate(id, req.body, { new: true }); // Encuentra y actualiza el libro por su ID

        if (!updatedBook) {
            return res.status(404).json({ message: 'Libro no encontrado' });
        }

        res.json(updatedBook); // Devuelve el libro actualizado en formato JSON
    } catch (error) {
        console.error(error);
        res.status(500).json({ message: 'Error interno del servidor' });
    }
});

router.delete('/delete-book/:id', async (req, res) => {
    const { id } = req.params;

    try {
        // Buscar el libro por su ID y borrarlo
        const deletedBook = await Book.findByIdAndDelete(id);

        if (!deletedBook) {
            return res.status(404).json({ message: "Libro no encontrado" });
        }

        res.json({ message: "Libro eliminado correctamente", deletedBook });
    } catch (error) {
        console.error("Error al borrar el libro:", error);
        res.status(500).json({ message: "Error al borrar el libro" });
    }
});

export default router;
