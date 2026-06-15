const path = require("path");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const CopyWebpackPlugin = require("copy-webpack-plugin");
const devCerts = require("office-addin-dev-certs");

module.exports = async (_env, argv) => {
  const isDev = argv.mode === "development";
  const httpsOptions = isDev ? await devCerts.getHttpsServerOptions() : undefined;

  return {
    entry: {
      taskpane: "./src/taskpane.ts",
    },
    output: {
      path: path.resolve(__dirname, "dist"),
      filename: "[name].js",
      clean: true,
    },
    resolve: {
      extensions: [".ts", ".js"],
    },
    module: {
      rules: [
        {
          test: /\.ts$/,
          use: "ts-loader",
          exclude: /node_modules/,
        },
      ],
    },
    devServer: {
      static: {
        directory: path.join(__dirname, "dist"),
      },
      host: "localhost",
      port: 3000,
      https: httpsOptions,
      headers: {
        "Access-Control-Allow-Origin": "*",
      },
      hot: true,
    },
    plugins: [
      new HtmlWebpackPlugin({
        template: "./taskpane.html",
        filename: "taskpane.html",
        chunks: ["taskpane"],
      }),
      new CopyWebpackPlugin({
        patterns: [
          { from: "manifest.xml", to: "manifest.xml" },
          { from: "assets", to: "assets", noErrorOnMissing: true },
        ],
      }),
    ],
    devtool: isDev ? "source-map" : false,
  };
};
