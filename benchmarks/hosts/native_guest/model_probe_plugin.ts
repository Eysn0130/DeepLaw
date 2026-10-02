import native from "/runtime/probe-source/deeplaw-native.ts"

// A fixed public engineering probe never needs the Host's filesystem context.
export default {
  id: "deeplaw-fixed-native-probe",
  server: async (args: unknown) => {
    const hooks = await native.server(args)
    const original = hooks["experimental.chat.system.transform"]
    return {
      ...hooks,
      "experimental.chat.system.transform": async (input: unknown, output: any) => {
        await original(input, output)
        output.system.splice(
          0,
          output.system.length,
          "This is a public fixed model probe. Answer the user's request directly without tools.",
        )
      },
    }
  },
}
