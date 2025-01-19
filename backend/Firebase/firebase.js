import dotenv from 'dotenv';
dotenv.config();

import { initializeApp, applicationDefault } from 'firebase-admin/app';
import { getFirestore } from 'firebase-admin/firestore';

initializeApp({
    credential: applicationDefault()
});


const db = getFirestore();

export default db;

console.log(process.env.GOOGLE_APPLICATION_CREDENTIALS);