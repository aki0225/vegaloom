// Sequelize 的 Windows 测试加载器会导入本项不使用的 DB2 native driver。
// 这里只替换该单一模块，避免把依赖安装问题误判为 SQLite 行为失败。

const Module = require('node:module');

const originalLoad = Module._load;

Module._load = function loadWithoutUnusedNativeDrivers(request, parent, isMain) {
  if (request === 'ibm_db') {
    return {};
  }

  return originalLoad.call(this, request, parent, isMain);
};
