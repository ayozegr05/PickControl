// TopBar.tsx
import React, { useState, useEffect } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Animated } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons'; 
import { useRouter } from "expo-router";


const TopBar = () => {
  const [scrollY, setScrollY] = useState(new Animated.Value(0));
  const [visible, setVisible] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false); // Estado para el menú

   const router = useRouter();

  useEffect(() => {
    const scrollListener = scrollY.addListener(({ value }) => {
      if (value > 50 && visible) {
        setVisible(false);
      }
      if (value < -50 && !visible) {
        setVisible(true);
      }
    });

    return () => {
      scrollY.removeListener(scrollListener);
    };
  }, [scrollY, visible]);

  const toggleMenu = () => {
    setMenuOpen(!menuOpen);
  };

  return (
    <Animated.View style={[styles.topBar, { opacity: visible ? 1 : 0 }]}>
      <View style={styles.leftSection}>
        <TouchableOpacity style={styles.menuButton} onPress={toggleMenu}>
          <MaterialCommunityIcons name="menu" size={30} color="white" />
        </TouchableOpacity>
      </View>
      <View style={styles.centerSection}>
        <Text style={styles.title}>Bienvenido</Text>
      </View>
      {menuOpen && (
        <View style={styles.menuOptions}>
          <TouchableOpacity ><Text style={styles.menuText}>Mi Perfil</Text></TouchableOpacity>               
          <TouchableOpacity onPress={() => router.push("/screens/earns")}>
            <Text style={styles.menuText}>Mis Ganancias</Text>
          </TouchableOpacity>
          <TouchableOpacity><Text style={styles.menuText}>Cerrar Sesión</Text></TouchableOpacity>
        </View>
      )}
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  topBar: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: 60,
    backgroundColor: '#333',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 10,
    zIndex: 1,
  },
  leftSection: {
    flex: 1,
    justifyContent: 'center',
  },
  centerSection: {
    flex: 2,
    justifyContent: 'center',
    alignItems: 'center',
  },
  menuButton: {
    padding: 10,
  },
  title: {
    fontSize: 18,
    color: 'white',
  },
  menuOptions: {
    position: 'absolute',
    top: 60,
    right: 0,
    backgroundColor: '#333',
    padding: 20,
    width: 200,
    zIndex: 2,
  },
  menuText: {
    color: 'white',
    fontSize: 18,
    marginBottom: 10,
  },
});

export default TopBar;
