// TopBar.tsx
import React, { useState, useEffect } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Animated } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons'; 
import { useRouter } from "expo-router";
import { useAuth } from '../context/AuthContext';
import { Alert } from 'react-native';

const TopBar = () => {
  const [scrollY, setScrollY] = useState(new Animated.Value(0));
  const [visible, setVisible] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const router = useRouter();
  const { isAuthenticated, userName, logout } = useAuth();

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

  const handleLogout = async () => {
    try {
      // Llamar al endpoint de logout
      const response = await fetch(`${process.env.EXPO_PUBLIC_API_BASE_URL}/logout`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        throw new Error('Error al cerrar sesión');
      }

      // Eliminar el token y actualizar el estado
      await logout();
      setMenuOpen(false);
      
      // Mostrar mensaje de éxito
      Alert.alert(
        "Sesión Cerrada",
        "Has cerrado sesión correctamente",
        [
          { 
            text: "OK", 
            onPress: () => router.replace('/')
          }
        ]
      );
    } catch (error) {
      console.error('Error al cerrar sesión:', error);
      Alert.alert(
        "Error",
        "Hubo un problema al cerrar la sesión"
      );
    }
  };
  
  return (
    <Animated.View style={[styles.topBar, { opacity: visible ? 1 : 0 }]}>
      <View style={styles.leftSection}>
        <Text style={styles.title}>PickControl</Text>
      </View>
      <View style={styles.rightSection}>
        {!isAuthenticated ? (
          <>
            <TouchableOpacity 
              style={styles.authButton}
              onPress={() => router.push("/screens/login")}
            >
              <Text style={styles.authButtonText}>Login</Text>
            </TouchableOpacity>
            <TouchableOpacity 
              style={styles.authButton}
              onPress={() => router.push("/screens/register")}
            >
              <Text style={styles.authButtonText}>Register</Text>
            </TouchableOpacity>
          </>
        ) : (
          <View style={styles.userContainer}>
            <TouchableOpacity onPress={toggleMenu}>
              <MaterialCommunityIcons name="account-circle" size={24} color="#fff" />
            </TouchableOpacity>
            {isAuthenticated && (
              <Text style={styles.userName}>{userName}</Text>
            )}
          </View>
        )}
      </View>
      {menuOpen && (
        <View style={styles.menuOptions}>
          {isAuthenticated ? (
            <>
              <TouchableOpacity>
                <Text style={styles.menuText}>Mi Perfil</Text>
              </TouchableOpacity>               
              <TouchableOpacity onPress={() => router.push("/screens/earns")}>
                <Text style={styles.menuText}>Mis Ganancias</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={handleLogout}>
                <Text style={styles.menuText}>Cerrar Sesión</Text>
              </TouchableOpacity>
            </>
          ) : null}
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
    backgroundColor: '#2d2d2d',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 15,
    zIndex: 1000,
    elevation: 5,
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    borderBottomWidth: 1,
    borderBottomColor: '#3d3d3d',
  },
  leftSection: {
    flex: 1,
    alignItems: 'flex-start',
    justifyContent: 'center',
  },
  rightSection: {
    flex: 1,
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: 8,
  },
  userContainer: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    color: '#ff9f1c',
    fontSize: 20,
    fontWeight: 'bold',
  },
  userName: {
    color: 'white',
    fontSize: 12,
    marginTop: 2,
  },
  menuOptions: {
    position: 'absolute',
    top: 60,
    right: 0,
    backgroundColor: '#3d3d3d',
    width: 200,
    padding: 10,
    borderRadius: 8,
    elevation: 5,
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
    borderWidth: 1,
    borderColor: '#3d3d3d',
  },
  menuText: {
    color: '#fff',
    padding: 10,
    fontSize: 16,
  },
  authButton: {
    backgroundColor: 'transparent',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#ff9f1c',
    marginLeft: 1,
    elevation: 2,
    shadowColor: '#000',
    shadowOffset: {
      width: 0,
      height: 1,
    },
    shadowOpacity: 0.2,
    shadowRadius: 1.41,
  },
  authButtonText: {
    color: '#ff9f1c',
    fontSize: 14,
    fontWeight: '600',
    textAlign: 'center',
  },
});

export default TopBar;
