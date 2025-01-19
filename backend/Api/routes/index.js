import db from "../../Firebase/firebase.js";
import { Router } from "express";

const router = Router();


router.get("/books", async (req, res) => {

    try {
        const querySnapshot = await db.collection('Books').get()
        const books =querySnapshot.docs.map(doc =>({
            id: doc.id,
            ...doc.data()
        }))
        res.status(200).json({ books });
        // res.render('index', {books})
        console.log("LIIIBROOOS: ", books)
    } catch (error) {
        console.error("Error al obtener libros: ", error);
        res.status(400).json({ error: "Solicitud incorrecta, verifica los datos proporcionados" })
    }
});

router.post('/new-book', async (req, res) => {

    try{
        const {title, author, price, synopsis, genre, date, photo} = req.body

        if (!title || !author || !price || !synopsis || !genre || !date || !photo) {
            res.status(400).json({ error: 'Datos incompletos. Todos los campos son obligatorios.' });
            return;
        }

        const newBookRef = await db.collection('Books').add({
            title,
            author,
            price,
            synopsis,
            genre,
            date,
            photo
        })
        res.status(200).json({ 
            message: "Libro publicado exitosamente",
            bookId: newBookRef.id
        })
        // res.redirect('/')
        // console.log("Este es el req body de post: ", req.body)
    } catch (error) {
        console.error("Error al publicar el libro: ", error);
        res.status(400).json({ error: "Solicitud incorrecta, verifica los datos proporcionados" })
    }
})

router.get("/book/:id", async (req, res) => {
    try {
        const doc = await db.collection('Books').doc(req.params.id).get()
        if (!doc.exists) {
            res.status(404).json({ error: 'Libro no encontrado' });
            return;
        }
        res.status(200).json({ book: { id: doc.id, ...doc.data() } });
        // res.render('index', { books: {id: doc.id, ...doc.data()}})
    } catch (error) {
        console.error("Error al obtener el libro: ", error);
        res.status(400).json({ error: "Solicitud incorrecta, verifica los datos proporcionados" })
    }
})

router.delete('/delete-book/:id', async (req, res) => {
    try{
        const bookId = req.params.id;
        if (!bookId) {
            res.status(400).json({ error: 'ID del libro no proporcionado' });
            return;
        }

        await db.collection('Books').doc(bookId).delete();
        res.status(200).json({ message: 'Libro eliminado exitosamente' });
        // res.redirect('/')
    } catch (error) {
        console.error("Error al eliminar el libro: ", error);
        res.status(400).json({ error: "Solicitud incorrecta, verifica los datos proporcionados" })
    }
})

router.put('/update-book/:id', async (req, res) => {
    try {
        const bookId = req.params.id;
        const updatedData = req.body;

        if (!bookId) {
            res.status(400).json({ error: 'ID del libro no proporcionado' });
            return;
        }
        
        await db.collection('Books').doc(bookId).update(updatedData);
        res.status(200).json({ message: 'Libro actualizado exitosamente' });
        // res.redirect('/')
    } catch (error) {
        console.error("Error al actualizar el libro: ", error);
        res.status(400).json({ error: "Solicitud incorrecta, verifica los datos proporcionados" })}
})

export default router