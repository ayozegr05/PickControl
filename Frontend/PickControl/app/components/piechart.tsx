import React from "react";
import { View, StyleSheet } from "react-native";
import { VictoryChart, VictoryBar, VictoryTheme } from 'victory-native';

const chardata = [
  { quarter: 1, earnings: 13000 },
  { quarter: 2, earnings: 16500 },
  { quarter: 3, earnings: 14250 },
  { quarter: 4, earnings: 19000 }
];

export default function ChartScreen ()  {
  return (
    <View style={styles.container}>
      <VictoryChart width={350} theme={VictoryTheme.clean}>
        <VictoryBar data={chardata} x="quarter" y="earnings" />
      </VictoryChart>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#fff"
  }
});


