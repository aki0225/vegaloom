// Sequelize 的 Windows 测试加载器会导入本项不使用的 DB2 与 IBM i native driver。
// 这里只替换这两个顶层模块，避免把无关依赖安装问题误判为 SQLite 行为失败。

const Module = require('node:module');

const originalLoad = Module._load;
const unusedNativeDrivers = new Set(['ibm_db', 'odbc']);

Module._load = function loadWithoutUnusedNativeDrivers(request, parent, isMain) {
  if (unusedNativeDrivers.has(request)) {
    return {};
  }

  return originalLoad.call(this, request, parent, isMain);
};
