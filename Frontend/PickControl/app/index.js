import { View, StyleSheet } from 'react-native';
import Main from './screens/HomeScreen';

export default function App() {
  return (
    <View style={styles.container}>
      <Main/>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  }
});