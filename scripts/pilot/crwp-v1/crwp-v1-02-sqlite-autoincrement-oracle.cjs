#!/usr/bin/env node

// 独立验证 Sequelize 在 SQLite alter 重建后保留 AUTOINCREMENT 语义。

const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');

function parseRepoArgument(argv) {
  const index = argv.indexOf('--repo');
  if (index === -1 || index + 1 >= argv.length) {
    throw new Error('missing_repo_argument');
  }

  return path.resolve(argv[index + 1]);
}

async function tableSql(sequelize, QueryTypes, tableName) {
  const rows = await sequelize.query(
    'SELECT sql FROM sqlite_master WHERE type = \'table\' AND name = :tableName',
    {
      replacements: { tableName },
      type: QueryTypes.SELECT,
    },
  );
  return rows.length === 1 && typeof rows[0].sql === 'string' ? rows[0].sql : '';
}

function hasAutoincrement(sql) {
  return /\bAUTOINCREMENT\b/i.test(sql);
}

async function runOracle(repo) {
  const requireFromRepo = createRequire(path.join(repo, 'package.json'));
  const { DataTypes, QueryTypes, Sequelize } = requireFromRepo('@sequelize/core');
  const { SqliteDialect } = requireFromRepo('@sequelize/sqlite3');
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'crwp-v1-02-'));
  const storage = path.join(tempRoot, 'oracle.sqlite');
  const sequelize = new Sequelize({
    dialect: SqliteDialect,
    storage,
    logging: false,
  });

  try {
    const Entry = sequelize.define(
      'OracleEntry',
      {
        id: {
          type: DataTypes.INTEGER,
          primaryKey: true,
          autoIncrement: true,
        },
        marker: {
          type: DataTypes.STRING,
          allowNull: false,
        },
      },
      {
        tableName: 'oracle_entries',
        timestamps: false,
      },
    );

    await sequelize.sync({ force: true });
    await Entry.bulkCreate([{ marker: 'first' }, { marker: 'second' }]);
    const beforeSql = await tableSql(sequelize, QueryTypes, 'oracle_entries');
    const rowsBefore = await Entry.findAll({ order: [['id', 'ASC']], raw: true });

    await sequelize.sync({ alter: true });
    const afterSql = await tableSql(sequelize, QueryTypes, 'oracle_entries');
    const description = await sequelize.queryInterface.describeTable('oracle_entries');
    const rowsAfter = await Entry.findAll({ order: [['id', 'ASC']], raw: true });

    await Entry.destroy({ where: { id: 2 } });
    const inserted = await Entry.create({ marker: 'third' });
    const insertedId = Number(inserted.get('id'));

    await sequelize.sync({ alter: true });
    const secondAlterSql = await tableSql(sequelize, QueryTypes, 'oracle_entries');
    const rowsAfterSecondAlter = await Entry.findAll({
      order: [['id', 'ASC']],
      raw: true,
    });

    await sequelize.query(
      'CREATE TABLE "plain_pk" ("id" INTEGER PRIMARY KEY, "note" TEXT)',
    );
    const plainDescription = await sequelize.queryInterface.describeTable('plain_pk');
    await sequelize.query(
      'CREATE TABLE "literal_pk" ('
        + '"id" INTEGER PRIMARY KEY, '
        + '"note" TEXT CHECK ("note" != \'AUTOINCREMENT\')'
        + ')',
    );
    const literalDescription = await sequelize.queryInterface.describeTable(
      'literal_pk',
    );
    await sequelize.query(
      'CREATE TABLE "line_comment_pk" ('
        + '"id" INTEGER PRIMARY KEY -- AUTOINCREMENT\n'
        + ', "note" TEXT'
        + ')',
    );
    const lineCommentSql = await tableSql(
      sequelize,
      QueryTypes,
      'line_comment_pk',
    );
    const originalShowConstraints = sequelize.queryInterface.showConstraints;
    let lineCommentDescription;
    try {
      sequelize.queryInterface.showConstraints = () => Promise.resolve([]);
      lineCommentDescription = await sequelize.queryInterface.describeTable(
        'line_comment_pk',
      );
    } finally {
      sequelize.queryInterface.showConstraints = originalShowConstraints;
    }
    await sequelize.query(
      'CREATE TABLE "block_comment_pk" ('
        + '"id" INTEGER PRIMARY KEY /* AUTOINCREMENT */, '
        + '"note" TEXT'
        + ')',
    );
    const blockCommentSql = await tableSql(
      sequelize,
      QueryTypes,
      'block_comment_pk',
    );
    const blockCommentDescription = await sequelize.queryInterface.describeTable(
      'block_comment_pk',
    );
    await sequelize.query(
      'CREATE TABLE "dash_pk" ('
        + '"note" TEXT DEFAULT \'a--b\', '
        + '"id" INTEGER PRIMARY KEY AUTOINCREMENT'
        + ')',
    );
    const dashDescription = await sequelize.queryInterface.describeTable('dash_pk');

    const result = {
      before_has_autoincrement: hasAutoincrement(beforeSql),
      after_has_autoincrement: hasAutoincrement(afterSql),
      second_alter_has_autoincrement: hasAutoincrement(secondAlterSql),
      describe_reports_autoincrement: description.id?.autoIncrement === true,
      data_preserved_after_first_alter:
        JSON.stringify(rowsAfter) === JSON.stringify(rowsBefore),
      inserted_id_after_deleting_max: insertedId,
      deleted_id_not_reused: insertedId > 2,
      data_preserved_after_second_alter:
        rowsAfterSecondAlter.length === 2
        && rowsAfterSecondAlter.some(row => row.id === 1 && row.marker === 'first')
        && rowsAfterSecondAlter.some(row => row.id === insertedId && row.marker === 'third'),
      plain_pk_not_autoincrement: plainDescription.id?.autoIncrement !== true,
      literal_text_not_autoincrement:
        literalDescription.id?.autoIncrement !== true
        && literalDescription.note?.autoIncrement !== true,
      line_comment_preserved_in_sql:
        lineCommentSql.includes('-- AUTOINCREMENT'),
      line_comment_not_autoincrement:
        lineCommentDescription.id?.primaryKey === true
        && lineCommentDescription.id?.autoIncrement !== true,
      block_comment_preserved_in_sql:
        blockCommentSql.includes('/* AUTOINCREMENT */'),
      block_comment_not_autoincrement:
        blockCommentDescription.id?.primaryKey === true
        && blockCommentDescription.id?.autoIncrement !== true,
      dash_literal_preserves_later_autoincrement:
        dashDescription.id?.autoIncrement === true
        && dashDescription.note?.autoIncrement !== true,
    };
    const passed = Object.entries(result).every(([key, value]) => {
      if (key === 'inserted_id_after_deleting_max') {
        return true;
      }
      return value === true;
    });
    return {
      status: passed ? 'passed' : 'failed',
      ...result,
    };
  } finally {
    await sequelize.close();
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}

async function main() {
  try {
    const repo = parseRepoArgument(process.argv.slice(2));
    const result = await runOracle(repo);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exitCode = result.status === 'passed' ? 0 : 1;
  } catch (error) {
    process.stdout.write(
      `${JSON.stringify(
        {
          status: 'error',
          error_type: error?.constructor?.name ?? 'Error',
          message: error instanceof Error ? error.message : String(error),
        },
        null,
        2,
      )}\n`,
    );
    process.exitCode = 2;
  }
}

void main();
